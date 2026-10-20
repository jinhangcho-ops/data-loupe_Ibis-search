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


@pytest.fixture
def folder():
    """테스트마다 빈 설정 폴더."""
    import settings
    shutil.rmtree(settings.PATH.parent, ignore_errors=True)
    settings.PATH.parent.mkdir(parents=True)
    return settings.PATH.parent


@pytest.fixture
def client(folder, monkeypatch):
    """프로그램 창처럼 열쇠를 가진 Flask 시험 클라이언트. 연결은 가짜로 바꿔 끼워 DB에 붙지 않는다(연결 요청 값만 확인한다)."""
    import collect_app  # noqa: F401  수집 라우트
    import prep_app  # noqa: F401  전처리 라우트
    import schedule_app  # noqa: F401  예약 라우트
    import server
    import source
    from app import app
    monkeypatch.setattr(server, "open_con", lambda info, read_only=True: object())
    monkeypatch.setattr(source, "list_schemas", lambda con: [])
    monkeypatch.setattr(source, "close", lambda con: None)
    server.state.update(con=None, passwords=[], opened=None, rows={})
    c = app.test_client()
    c.get("/?key=" + server.KEY)
    yield c
    server.state.update(con=None, passwords=[], opened=None, rows={})


def pytest_configure(config):
    config.addinivalue_line("filterwarnings", "ignore:fetch_arrow_table:DeprecationWarning")  # Ibis 안의 경고(우리 코드가 아님)
    config.addinivalue_line("filterwarnings", "ignore:fetch_record_batch:DeprecationWarning")  # 같음(CSV를 묶음으로 저장할 때)


def pytest_sessionfinish():  # pytest가 다 끝나면(받을 인자는 쓰지 않아 뺀다)
    shutil.rmtree(APPDATA, ignore_errors=True)
