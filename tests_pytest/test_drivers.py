"""드라이버 확인·안내: 대상마다 명령·설명, 상태 세 가지, 버전 일치, exe(frozen) 동작, 연결 실패에 안내가 실리는지."""
import re
import sys
from pathlib import Path

import pytest

from core import drivers
from core import server

ROOT = Path(__file__).resolve().parent.parent
PINS = ROOT / "constraints.txt"  # 드라이버 설치 명령의 -c(간접 의존 고정)


def pinned():
    return re.search(r"^ibis-framework\[[^\]]*\]==([\w.]+)", (ROOT / "requirements.txt").read_text(encoding="utf-8"), re.M).group(1)


@pytest.fixture(autouse=True)
def no_slow(monkeypatch):
    """이 PC의 파이썬·확장 상태에 기대지 않게 조사 함수를 고정한다."""
    monkeypatch.setattr(drivers, "has_python", lambda: True)
    monkeypatch.setattr(drivers, "extension_states", lambda: {"httpfs": True})


def test_every_target_has_command_and_explanation():
    items = drivers.overview()
    assert {d["group"] for d in items} == {"DB", "DW", "DL"}
    names = {d["key"] for d in items}
    assert {"bigquery", "snowflake", "databricks", "redshift", "synapse", "impala", "athena", "trino", "pyspark", "flink", "httpfs", "delta", "iceberg"} <= names
    for d in items:
        assert d["command"] and d["explanation"] and d["address"] and d["name"], d["key"]
        assert d["status"] in ("installed", "missing", "program_needed")
    assert set(drivers.DRIVERS) <= names  # 기존 대상이 빠지지 않는다.


def test_borrowed_targets_use_other_driver():
    by = {d["key"]: d for d in drivers.overview()}
    assert by["redshift"]["address"].startswith("postgres://") and "[postgres]" in by["redshift"]["command"]
    assert "[mssql]" in by["synapse"]["command"] and by["synapse"]["program"]["name"].startswith("ODBC")


def test_three_statuses(monkeypatch):
    found = {"psycopg", "pyodbc", "pyspark"}  # 있는 모듈
    monkeypatch.setattr(drivers, "module_found", lambda m: m in found)
    monkeypatch.setattr(drivers, "has_java", lambda: False)
    monkeypatch.setattr(drivers, "mssql_driver", lambda: "ODBC Driver 18 for SQL Server")
    assert drivers.entry("postgres")["status"] == "installed"
    assert drivers.entry("mysql")["status"] == "missing"
    spark = drivers.entry("pyspark")
    assert spark["status"] == "program_needed" and spark["program"]["name"].startswith("Java")
    assert drivers.entry("mssql")["status"] == "installed"
    monkeypatch.setattr(drivers, "mssql_driver", lambda: None)
    assert drivers.entry("mssql")["status"] == "program_needed"


def test_duckdb_extensions_checked_as_extensions(monkeypatch):
    monkeypatch.setattr(drivers, "extension_states", lambda: {"httpfs": True, "delta": False})
    by = {d["key"]: d for d in drivers.overview()}
    assert by["httpfs"]["status"] == "installed" and by["delta"]["status"] == "missing" and by["iceberg"]["status"] == "missing"
    assert "INSTALL delta" in by["delta"]["command"] and "pip" not in by["delta"]["command"]


def test_extension_states_reads_duckdb(monkeypatch):
    monkeypatch.undo()
    found = drivers.extension_states()  # duckdb_extensions()를 묻는다(받지 않는다).
    assert found and all(isinstance(v, bool) for v in found.values()) and {"httpfs", "delta", "iceberg"} <= set(found) | {"httpfs", "delta", "iceberg"}


def test_version_matches_requirements(monkeypatch):
    assert drivers.ibis_version() == pinned()
    assert PINS.is_file() and drivers.pip_command("bigquery") == f'pip install -c "{PINS}" "ibis-framework[bigquery]=={pinned()}"'
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert pinned() in drivers.pip_command("bigquery")


def test_frozen_adds_driver_folder_only_if_exists(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "path", list(sys.path))
    drivers.use_installed()
    assert str(drivers.driver_folder()) not in sys.path  # 폴더가 없으면 더하지 않는다.
    drivers.driver_folder().mkdir(parents=True)
    drivers.use_installed()
    assert sys.path.count(str(tmp_path / "DataLoupe" / "drivers")) == 1
    drivers.use_installed()
    assert sys.path.count(str(drivers.driver_folder())) == 1


def test_source_run_does_not_touch_path(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(sys, "path", list(sys.path))
    drivers.driver_folder().mkdir(parents=True)
    drivers.use_installed()
    assert str(drivers.driver_folder()) not in sys.path


def test_frozen_command_and_restart_note(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(drivers, "module_found", lambda m: False)
    d = drivers.entry("snowflake")
    assert d["command"] == f'py -3.14 -m pip install --target "%APPDATA%\\DataLoupe\\drivers" -c "{PINS}" "ibis-framework[snowflake]=={pinned()}"'
    assert "DataLoupe" in d["after"] and "drivers" in d["after"] and d["after"].endswith("설치 뒤 프로그램을 다시 켜세요.") and d["notice"] == ""  # Python이 있어도 넣을 폴더를 알린다


def test_frozen_without_python_shows_notice(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(drivers, "has_python", lambda: False)
    monkeypatch.setattr(drivers, "module_found", lambda m: False)
    d = drivers.entry("snowflake")
    assert "이 exe에는 Snowflake 드라이버가 없어요" in d["notice"]
    assert "Python 3.14를 설치하고 받는 곳의 패키지를" in d["notice"] and "DataLoupe" in d["notice"] and drivers.PYTHON_URL in d["notice"]
    monkeypatch.delattr(sys, "frozen")
    assert drivers.entry("snowflake")["notice"] == ""  # 소스 실행에는 안내가 없다.


def test_has_python_looks_at_launcher_then_path(monkeypatch):
    monkeypatch.undo()
    monkeypatch.setattr(drivers.shutil, "which", lambda n: None)
    drivers.has_python.cache_clear()  # 실행마다 한 번만 묻고 기억하므로 시험마다 비운다
    assert drivers.has_python() is False
    drivers.has_python.cache_clear()

    class Done:
        stdout, stderr = " -V:3.14 *   Python 3.14\n", ""
    monkeypatch.setattr(drivers.shutil, "which", lambda n: n if n == "py" else None)
    monkeypatch.setattr(drivers.subprocess, "run", lambda *a, **k: Done)
    assert drivers.has_python() is True


def gone(monkeypatch, scheme):
    """그 대상의 Ibis 모듈이 없는 것처럼: prepare는 importlib로 직접 불러 보므로, 드라이버가 깔린 PC(CI 빌드)에서도 sys.modules에 None을 넣어 불러오기를 막는다."""
    monkeypatch.setattr(drivers, "module_found", lambda m: False)
    monkeypatch.setitem(sys.modules, drivers.DRIVERS[scheme][1], None)


def test_connect_error_carries_driver_info(monkeypatch):
    gone(monkeypatch, "snowflake")
    with pytest.raises(drivers.DriverError) as raised:
        drivers.prepare("snowflake://acct/db/schema")
    info = raised.value.driver
    assert info["status"] == "missing" and info["key"] == "snowflake" and info["command"] in str(raised.value)
    assert pinned() in info["command"] and info["explanation"]


def test_connect_route_returns_driver_object(client, monkeypatch):
    def fail(url, user="", password=""):
        gone(monkeypatch, "bigquery")
        drivers.prepare(url, password)
    monkeypatch.setattr(server, "open_con", lambda info, read_only=True: fail(info["url"]))
    res = client.post("/api/connect", json={"kind": "url", "url": "bigquery://p/d"})
    body = res.get_json()
    assert res.status_code == 400 and body["driver"]["key"] == "bigquery" and body["driver"]["command"] and body["error"]


def test_drivers_route(client):
    res = client.get("/api/drivers")
    items = res.get_json()["drivers"]
    assert res.status_code == 200 and len(items) == len(drivers.keys()) and all(d["command"] for d in items)
    assert "/api/drivers" in server.FREE


def test_supabase_entry_borrows_postgres():
    info = drivers.entry("supabase")
    assert info["group"] == "DB" and "pooler.supabase.com:5432" in info["address"] and "Session pooler" in info["explanation"]
    assert "[postgres]" in info["command"] and "6543" in info["explanation"]


@pytest.mark.parametrize("url,expected", [
    ("postgres://u@aws-0-ap.pooler.supabase.com:5432/postgres", {"connect_timeout": drivers.PG_WAIT}),
    ("postgres://u@aws-0-ap.pooler.supabase.com:6543/postgres", {"connect_timeout": drivers.PG_WAIT, "prepare_threshold": None}),
    ("postgres://u@h:5432/postgres?pgbouncer=true", {"connect_timeout": drivers.PG_WAIT, "prepare_threshold": None, "pgbouncer": None}),
    ("postgres://u@h:5432/postgres?connect_timeout=3", {}),  # 주소에 있으면 그것을 쓴다
])
def test_prepare_pooler_turns_off_prepared_statements(url, expected):
    assert drivers.prepare(url) == expected


def test_open_url_supabase_paste(monkeypatch):
    """붙여넣은 문자열의 [YOUR-PASSWORD]는 빠지고, 칸의 비밀번호가 들어가며, 6543이면 prepare_threshold=None, sslmode는 주소에 그대로 남는다."""
    from core import source
    seen = []
    monkeypatch.setattr(source.ibis, "connect", lambda url, **kw: seen.append((url, kw)) or "con")
    monkeypatch.setattr(source, "within", lambda seconds, work, *a, **k: work())
    for port in (5432, 6543):
        source.open_url(f"postgresql://postgres.abc:[YOUR-PASSWORD]@aws-0-ap.pooler.supabase.com:{port}/postgres?sslmode=require", "", "p@ss")
    (url1, kw1), (url2, kw2) = seen
    assert url1 == "postgresql://postgres.abc:p%40ss@aws-0-ap.pooler.supabase.com:5432/postgres?sslmode=require" and kw1 == {"connect_timeout": drivers.PG_WAIT}
    assert "YOUR-PASSWORD" not in url2 and kw2 == {"connect_timeout": drivers.PG_WAIT, "prepare_threshold": None}


def test_postgres_timeout_reads_in_korean(monkeypatch):
    """psycopg의 ConnectionTimeout(영어)은 한국어 안내로 바뀐다."""
    from core import source
    ConnectionTimeout = type("ConnectionTimeout", (Exception,), {})
    monkeypatch.setattr(source.ibis, "connect", lambda url, **kw: (_ for _ in ()).throw(ConnectionTimeout("connection timeout expired")))
    monkeypatch.setattr(source, "within", lambda seconds, work, *a, **k: work())
    with pytest.raises(TimeoutError, match="접속 대기 시간 안에"):
        source.open_url("postgres://u@h:5432/db")


def test_windows_drive_paths_open_the_same_file(tmp_path):
    """duckdb://C:/…·sqlite:///C:/… 도 그 파일을 연다(예전에는 Ibis가 C:Users… 드라이브 상대 경로를 만들어 못 열거나 새 DB를 만들었다)."""
    import sqlite3
    import duckdb
    from core import source
    lite, duck = tmp_path / "r.db", tmp_path / "k.duckdb"
    sqlite3.connect(lite).execute("create table t (a)").connection.commit()
    duckdb.connect(str(duck)).execute("create table u (a int)").close()
    slash = lambda p: str(p).replace("\\", "/")
    for url, table in ((f"sqlite:///{slash(lite)}", "t"), (f"sqlite://{slash(lite)}", "t"), (f"duckdb:///{slash(duck)}?read_only=true", "u")):
        fixed = source.clean_url(url)[0]
        assert source.within(30, lambda: source.ibis.connect(fixed).list_tables(), "late") == [table], url
    assert source.drive_path("duckdb://data.duckdb") == "duckdb://data.duckdb" and source.drive_path("postgres://h:5432/db") == "postgres://h:5432/db"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["k.duckdb", "r.db"]  # 엉뚱한 새 DB 파일이 생기지 않는다


def test_psycopg_gets_prepare_threshold_and_sslmode(monkeypatch):
    """ibis.connect가 주소 쿼리와 추가 인자를 psycopg.connect로 넘기는지(psycopg.connect는 가짜)."""
    import ibis
    import psycopg
    got = {}
    monkeypatch.setattr(psycopg, "connect", lambda **kw: got.update(kw) or (_ for _ in ()).throw(RuntimeError("stop")))
    with pytest.raises(RuntimeError):
        ibis.connect("postgres://u:pw@h:6543/db?sslmode=require", prepare_threshold=None)
    assert got["sslmode"] == "require" and got["prepare_threshold"] is None and got["port"] == 6543


def test_clean_url_without_placeholder_untouched():
    from core import source
    assert source.clean_url("postgres://u@h:5432/db") == ("postgres://u@h:5432/db", "", "")
    assert source.clean_url("postgres://u:real@h/db") == ("postgres://u:real@h/db", "", "")


def test_connect_route_strips_placeholder_before_anything_parses_it(client, monkeypatch):
    """[YOUR-PASSWORD]가 든 주소가 passwords()·recent()의 urlsplit에서 터지지 않는다(대괄호는 IPv6 표기로 읽힌다)."""
    seen = []
    monkeypatch.setattr(server, "open_con", lambda info, read_only=True: seen.append(info) or (_ for _ in ()).throw(ValueError("거부")))
    res = client.post("/api/connect", json={"kind": "url", "url": "postgresql://postgres.a:[YOUR-PASSWORD]@127.0.0.1:6543/x", "password": "x"})
    assert res.status_code == 400 and res.get_json()["error"] == "거부"
    assert seen[0]["url"] == "postgresql://postgres.a@127.0.0.1:6543/x"


def test_every_driver_points_to_where_to_get_it():
    by = {d["key"]: d for d in drivers.overview()}
    assert by["snowflake"]["url"] == "https://pypi.org/project/snowflake-connector-python/"
    assert by["redshift"]["url"] == "https://pypi.org/project/psycopg/"  # 빌려 쓰는 드라이버의 패키지
    assert by["delta"]["url"].startswith("https://duckdb.org/") and by["sqlite"]["url"] is None  # 파이썬에 들어 있는 것은 받을 곳이 없다
    assert all(d["url"] is None or d["url"].startswith("https://") for d in by.values())
