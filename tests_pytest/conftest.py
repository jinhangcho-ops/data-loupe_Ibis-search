"""pytest 공통 준비: 프로젝트 폴더를 import 경로에 넣고, 설정 폴더(APPDATA)를 임시 폴더로 바꾼다. 실제 사용자 설정은 건드리지 않는다.
데이터는 테스트 안에서 지어내 쓰고 저장소에 두지 않는다."""
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
APPDATA = tempfile.mkdtemp(prefix="ibis_pytest_")
os.environ["APPDATA"] = APPDATA  # settings.PATH는 import할 때 정해지므로 프로젝트 모듈보다 먼저 바꾼다.

# 라우트 모듈은 Flask 앱이 첫 요청을 받기 전에 모두 올려 둔다(앞선 시험이 요청을 보낸 뒤에 올리면 '라우트를 더할 수 없다'는 오류가 나므로, 일부만 돌리거나 순서를 섞어도 같게).
from integrations import bot_app  # noqa: E402,F401  Slack 봇 라우트
from collect import collect_app  # noqa: E402,F401  수집 라우트
from dashboard import dash_app  # noqa: E402,F401  대시보드 라우트
from core import draft_app  # noqa: E402,F401  화면 임시 저장(초안) 라우트
from query import flow_app  # noqa: E402,F401  문장·내 지표·공통 키 라우트
from metrics import catalog_app  # noqa: E402,F401  지표 사전 라우트
from integrations import integrations_app  # noqa: E402,F401  연동 라우트
from prep import prep_app  # noqa: E402,F401  전처리 라우트
from integrations import schedule_app  # noqa: E402,F401  예약 라우트
from integrations import notify  # noqa: E402,F401  보내기 라우트


@pytest.fixture(autouse=True)
def csv_checked_every_call(monkeypatch):
    """refresh_csv는 실제로는 1초에 한 번만 CSV를 확인한다. 시험은 CSV를 고친 바로 뒤에 읽으므로 매번 확인하게 한다."""
    from core import source
    monkeypatch.setattr(source, "CHECK_EVERY", 0)


@pytest.fixture(autouse=True)
def no_setup_running(monkeypatch):
    """시험마다 띄운 설치 프로그램이 없는 상태에서 시작한다(가짜 설치 프로그램은 끝나지 않는다)."""
    from core import features
    monkeypatch.setitem(features.SETUP, "run", None)


@pytest.fixture
def folder():
    """테스트마다 빈 설정 폴더. 끝난 뒤에도 비워서 저장한 설정(week_start 등)이 다음 시험으로 새지 않게 한다."""
    from core import settings
    shutil.rmtree(settings.PATH.parent, ignore_errors=True)
    settings.PATH.parent.mkdir(parents=True)
    yield settings.PATH.parent
    shutil.rmtree(settings.PATH.parent, ignore_errors=True)
    settings.PATH.parent.mkdir(parents=True)


@pytest.fixture
def client(folder, monkeypatch):
    """프로그램 창처럼 열쇠를 가진 Flask 시험 클라이언트. 연결은 가짜로 바꿔 끼워 DB에 붙지 않는다(연결 요청 값만 확인한다)."""
    from core import server
    from core import source
    from query.search_app import app
    monkeypatch.setattr(server, "open_con", lambda info, read_only=True: object())
    monkeypatch.setattr(source, "list_schemas", lambda con: [])
    monkeypatch.setattr(source, "close", lambda con: None)
    server.state.update(con=None, passwords=[], opened=None, rows={})
    server.forget_tables()  # 테이블 이름으로 기억한 목록·스키마(시험은 연결을 바로 바꿔 끼운다)
    c = app.test_client()
    c.get("/?key=" + server.KEY)
    yield c
    server.state.update(con=None, passwords=[], opened=None, rows={})
    server.forget_tables()


def spy_queries(monkeypatch):
    """연결(DuckDB)로 나가는 조회 글을 모으는 목록을 돌려준다: 표·값 하나(Scalar)·열의 실행과 raw_sql을 가로챈다. 원격 DB에 COUNT 같은 조회를 보내지 않았는지 볼 때 쓴다."""
    from ibis.backends.duckdb import Backend
    from ibis.expr.types import Column, Scalar, Table
    from lazy import ibis
    found = []
    for cls, names in ((Table, ("to_pyarrow", "to_polars", "execute", "to_pyarrow_batches")), (Scalar, ("to_pyarrow", "execute")), (Column, ("to_pyarrow", "execute"))):
        for name in names:
            original = getattr(cls, name)
            monkeypatch.setattr(cls, name, lambda self, *a, _o=original, **k: (found.append(str(ibis.to_sql(self))), _o(self, *a, **k))[1])
    raw = Backend.raw_sql
    monkeypatch.setattr(Backend, "raw_sql", lambda self, query, **k: (found.append(str(query)), raw(self, query, **k))[1])
    return found


def pytest_configure(config):
    config.addinivalue_line("filterwarnings", "ignore:fetch_arrow_table:DeprecationWarning")  # Ibis 안의 경고(우리 코드가 아님)
    config.addinivalue_line("filterwarnings", "ignore:fetch_record_batch:DeprecationWarning")  # 같음(CSV를 묶음으로 저장할 때)
    sweep_edges()  # 앞선 실행이 남긴 헤드리스 Edge 정리


def pytest_sessionfinish():  # pytest가 다 끝나면(받을 인자는 쓰지 않아 뺀다)
    shutil.rmtree(APPDATA, ignore_errors=True)


def dialect_status(id, dialect):
    """함수 사전의 id를 방언(sqlglot 이름)으로 SQL로 만들 수 있는지(실행하지 않음) {status, note}. 시험 표는 모든 종류의 컬럼을 가진 빈 표."""
    from lazy import ibis
    from core import sql_funcs
    t = ibis.table({"n": "float64", "i": "int64", "s": "string", "d": "timestamp", "dd": "date", "b": "boolean", "j": "json", "a": "array<string>",
                    "st": "struct<k: string>"}, name="probe")

    def works(expr):
        try:
            ibis.to_sql(expr, dialect=dialect)
            return True
        except Exception:
            return False
    return sql_funcs.judge(sql_funcs.get(id), t, works)


# ---------- 헤드리스 Edge: 띄우기·끄기 ----------
# --headless=new Edge는 처음 띄운 프로세스를 끝내고 다른 프로세스로 다시 뜬다. 그래서 Popen 손잡이의 kill()은 빈 손이고 본체·GPU가 남는다.
# 또 시험이 중간에 끊기면(강제 종료) finally의 kill()에 못 간다. 두 경우 모두 막는다: Job 객체(pytest가 죽으면 Edge 트리도 같이 죽음) + 프로필 경로로 끄기.
EDGE_PROFILES = ("u2edge_", "q1edge_", "q2edge_", "p1edge_", "a5edge_", "hqedge_", "dl_exe_check_")  # 우리가 만든 헤드리스 프로필 폴더 이름(이것만 끈다)
_JOB = None


def _job():
    """pytest 프로세스가 끝나면(어떻게 끝나든) 안의 프로세스를 모두 끄는 Windows Job 객체(한 번만 만든다). 윈도가 아니면 None."""
    global _JOB
    if _JOB is None and sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class Basic(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64), ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD), ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

        class Extended(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", ctypes.c_uint64 * 6), ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

        k = ctypes.windll.kernel32
        h = k.CreateJobObjectW(None, None)
        info = Extended()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        k.SetInformationJobObject(h, 9, ctypes.byref(info), ctypes.sizeof(info))  # 9 = JobObjectExtendedLimitInformation
        _JOB = h
    return _JOB


def spawn_edge(cmd):
    """헤드리스 Edge를 띄운다. cmd의 --user-data-dir 폴더 이름은 EDGE_PROFILES 중 하나로 시작해야 한다(kill_edge·sweep_edges가 그 경로로 끈다).
    프로필 폴더에 주인(이 프로세스) PID를 적어 두어, 동시에 도는 다른 pytest의 sweep_edges가 살아 있는 우리 Edge를 끄지 않게 한다."""
    import subprocess
    profile = next((a.split("=", 1)[1] for a in cmd if a.startswith("--user-data-dir=")), None)
    if profile:
        Path(profile).mkdir(parents=True, exist_ok=True)
        (Path(profile) / "owner.pid").write_text(str(os.getpid()), encoding="ascii")
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if _job() is not None:
        import ctypes
        ctypes.windll.kernel32.AssignProcessToJobObject(_job(), int(proc._handle))  # 이 뒤에 Edge가 다시 띄우는 프로세스도 같은 Job에 든다
    return proc


def kill_edge(profile):
    """프로필 폴더(profile)를 쓰는 msedge 프로세스를 모두 끈다(사용자가 쓰는 Edge는 다른 프로필이라 건드리지 않는다)."""
    import psutil
    mine = str(profile)
    for p in psutil.process_iter(["name", "cmdline"]):
        try:
            if (p.info["name"] or "").lower() == "msedge.exe" and mine in " ".join(p.info["cmdline"] or []):
                p.kill()
        except psutil.Error:
            pass


def _orphan(line):
    """헤드리스 Edge 명령줄의 프로필 폴더에 적힌 주인 PID가 죽었으면(또는 없으면) 참. 살아 있는 다른 pytest의 Edge는 건드리지 않는다."""
    import re
    import psutil
    m = re.search(r'--user-data-dir="?([^" ]+)', line)
    try:
        pid = int((Path(m.group(1)) / "owner.pid").read_text(encoding="ascii")) if m else 0
    except (OSError, ValueError):
        return True  # 주인을 적지 않은 옛 실행 방식
    return not psutil.pid_exists(pid)


def sweep_edges():
    """앞선 실행이 남긴 헤드리스 Edge(EDGE_PROFILES 폴더를 쓰고 주인 pytest가 죽은 것)를 끈다. pytest 시작 때 한 번. 끈 수를 돌려준다."""
    import psutil
    n = 0
    for p in psutil.process_iter(["name", "cmdline"]):
        try:
            line = " ".join(p.info["cmdline"] or [])
            if (p.info["name"] or "").lower() == "msedge.exe" and "--headless" in line and any(x in line for x in EDGE_PROFILES) and _orphan(line):
                p.kill()
                n += 1
        except psutil.Error:
            pass
    return n
