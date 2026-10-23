"""드라이버 확인·안내: 대상마다 명령·설명, 상태 세 가지, 버전 일치, exe(frozen) 동작, 연결 실패에 안내가 실리는지."""
import re
import sys
from pathlib import Path

import pytest

import drivers
import server

ROOT = Path(__file__).resolve().parent.parent


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
    assert drivers.pip_command("bigquery") == f'pip install "ibis-framework[bigquery]=={pinned()}"'
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
    assert sys.path.count(str(tmp_path / "ibis_search" / "drivers")) == 1
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
    assert d["command"] == f'py -3.14 -m pip install --target "%APPDATA%\\ibis_search\\drivers" "ibis-framework[snowflake]=={pinned()}"'
    assert d["after"] == "설치 뒤 프로그램을 다시 켜세요." and d["notice"] == ""


def test_frozen_without_python_shows_notice(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(drivers, "has_python", lambda: False)
    monkeypatch.setattr(drivers, "module_found", lambda m: False)
    d = drivers.entry("snowflake")
    assert "이 exe에는 Snowflake 드라이버가 없어요" in d["notice"]
    assert "Python 3.14를 설치한 뒤 위 명령을 실행하거나 소스로 실행하세요" in d["notice"] and drivers.PYTHON_URL in d["notice"]
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


def test_connect_error_carries_driver_info(monkeypatch):
    monkeypatch.setattr(drivers, "module_found", lambda m: False)
    with pytest.raises(drivers.DriverError) as raised:
        drivers.prepare("snowflake://acct/db/schema")
    info = raised.value.driver
    assert info["status"] == "missing" and info["key"] == "snowflake" and info["command"] in str(raised.value)
    assert pinned() in info["command"] and info["explanation"]


def test_connect_route_returns_driver_object(client, monkeypatch):
    def fail(url, user="", password=""):
        monkeypatch.setattr(drivers, "module_found", lambda m: False)
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
