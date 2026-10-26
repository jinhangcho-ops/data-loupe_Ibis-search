"""14차 B: DB 쪽 시간 제한·접속 이름(settle), 취소를 알리지 못한 연결은 새로 열기(server.run), SQL Server autocommit·APP, 드라이버 설치 명령의 -c constraints.txt.
드라이버가 없는 PC라 접속은 가짜 백엔드로 보고, 드라이버 모듈은 sys.modules에 빈 모듈을 넣어 불러오기만 통과시킨다."""
import sys
import threading
import time
import types
from pathlib import Path

import pytest

from core import drivers
from core import server
from core import source

ROOT = Path(__file__).resolve().parent.parent


class Fake:
    """ibis 백엔드 흉내: raw_sql로 보낸 문장을 적어 둔다."""

    def __init__(self, url="", **kw):
        self.sent, self.kw = [], kw
        self.client = types.SimpleNamespace(default_query_job_config=types.SimpleNamespace())

    def raw_sql(self, sql):
        self.sent.append(sql)
        return types.SimpleNamespace(close=lambda: None)


def opened(monkeypatch, url, module=None):
    if module:
        monkeypatch.setitem(sys.modules, module, types.ModuleType(module))
    monkeypatch.setattr(source.ibis, "connect", lambda url, **kw: Fake(url, **kw))
    monkeypatch.setattr(source, "within", lambda seconds, work, *a, **k: work())
    return source.open_url(url, "", "pw")


def test_postgres_session_timeout_and_name(monkeypatch):
    sent = opened(monkeypatch, "postgres://u@h:5432/db").sent
    assert sent == [f"SET statement_timeout = {server.QUERY_TIMEOUT * 1000}", "SET application_name = 'DataLoupe'"]
    assert opened(monkeypatch, "postgres://u@h:5432/db?application_name=mine").sent == [f"SET statement_timeout = {server.QUERY_TIMEOUT * 1000}"]  # 주소에 넣은 이름은 그대로
    assert opened(monkeypatch, "postgres://u@h:6543/db").sent == []  # Transaction pooler는 서버 연결을 나눠 써서 건드리지 않는다


@pytest.mark.parametrize("url,module,expected", [
    ("mysql://u@h:3306/x", "MySQLdb", "SET SESSION max_execution_time = 1800000"),
    ("singlestoredb://u@h:3306/x", "singlestoredb", "SET SESSION max_execution_time = 1800000"),
    ("snowflake://acct/db/schema", "snowflake.connector", "ALTER SESSION SET STATEMENT_TIMEOUT_IN_SECONDS = 1800"),
])
def test_session_timeout_other_dbs(monkeypatch, url, module, expected):
    assert server.QUERY_TIMEOUT == 1800 and expected in opened(monkeypatch, url, module).sent


def test_bigquery_job_timeout(monkeypatch):
    con = opened(monkeypatch, "bigquery://p/d", "google.cloud.bigquery")
    assert con.client.default_query_job_config.job_timeout_ms == server.QUERY_TIMEOUT * 1000


def test_settle_failure_keeps_connection(monkeypatch):
    """시간 제한을 못 거는 DB(권한·MariaDB 등)여도 접속은 그대로 쓴다."""
    def boom(sql):
        raise RuntimeError("unknown variable")
    monkeypatch.setitem(sys.modules, "MySQLdb", types.ModuleType("MySQLdb"))
    monkeypatch.setattr(source.ibis, "connect", lambda url, **kw: types.SimpleNamespace(raw_sql=boom))
    monkeypatch.setattr(source, "within", lambda seconds, work, *a, **k: work())
    assert source.open_url("mysql://u@h:3306/x", "", "pw").raw_sql is boom


def test_mssql_autocommit_and_app(monkeypatch):
    odbc = types.ModuleType("pyodbc")
    odbc.drivers = lambda: ["ODBC Driver 18 for SQL Server"]
    monkeypatch.setitem(sys.modules, "pyodbc", odbc)
    assert drivers.prepare("mssql://h:1433/db") == {"autocommit": True, "APP": "DataLoupe", "driver": "ODBC Driver 18 for SQL Server"}
    assert drivers.prepare("mssql://h:1433/db?APP=Mine&driver=X") == {"autocommit": True}  # 주소에 넣은 이름·드라이버는 그대로


def test_pip_command_uses_constraints(monkeypatch):
    pins = f'-c "{ROOT / "constraints.txt"}"'
    assert pins in drivers.pip_command("postgres") and (ROOT / "constraints.txt").is_file()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert pins in drivers.pip_command("postgres") and "--target" in drivers.pip_command("postgres")
    assert '("constraints.txt", ".")' in (ROOT / "DataLoupe.spec").read_text(encoding="utf-8")  # exe에서는 같은 자리(_internal)에 들어간다


class NoCancel:
    """interrupt·cancel이 없는 드라이버 연결(MySQLdb·pyodbc 등). 이 연결에서 겹쳐 도는 작업 수를 센다."""
    busy = 0

    def run(self, seconds):
        self.busy += 1
        try:
            time.sleep(seconds)
            return self.busy
        finally:
            self.busy -= 1


def url_state(monkeypatch, reopen):
    old = types.SimpleNamespace(name="mysql", con=NoCancel())
    monkeypatch.setitem(server.state, "con", old)
    monkeypatch.setitem(server.state, "opened", {"kind": "url", "url": "mysql://h/x", "user": "u", "password": "pw"})
    monkeypatch.setitem(server.state, "passwords", ["pw"])
    monkeypatch.setattr(server, "open_con", reopen)
    return old


def cancel_soon():
    threading.Timer(0.3, lambda: server.state["cancel"].set()).start()


def test_cancel_without_interrupt_reopens(monkeypatch):
    """[취소]를 드라이버에 알리지 못하면 버린 스레드가 아직 연결을 쓴다 → 다음 작업은 메모리의 접속 정보로 새로 연 연결을 쓴다."""
    seen = []
    old = url_state(monkeypatch, lambda info: seen.append(info) or types.SimpleNamespace(name="mysql", con=NoCancel()))
    cancel_soon()
    with pytest.raises(ValueError, match="새로 열었습니다"):
        server.run(lambda: old.con.run(2))
    assert server.state["con"] is not old and seen[0]["password"] == "pw" and server.state["passwords"] == ["pw"]
    assert server.run(lambda: server.state["con"].con.run(0.1)) == 1  # 버린 작업과 겹치지 않는다
    time.sleep(2)


def test_cancel_reopen_fails_asks_reconnect(monkeypatch):
    def fail(info):
        raise OSError("down")
    old = url_state(monkeypatch, fail)
    cancel_soon()
    with pytest.raises(ValueError, match="연결을 다시 하세요"):
        server.run(lambda: old.con.run(2))
    assert server.state["con"] is None and server.state["opened"] is None
    with pytest.raises(ValueError, match="먼저 연결하세요"):
        server.get_con()
    time.sleep(2)
