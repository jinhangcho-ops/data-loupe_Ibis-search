"""쿼리·분석·수집 화면을 프로그램 창으로 연다. exe의 실행 파일이자 유일한 실행 방법.
'--browser'로 실행하면 서버·메인 창 없이 수집용 브라우저 창 프로세스만 돈다(collect_browser.launch가 띄운다).
'--run "구성 이름"'으로 실행하면 서버·메인 창 없이 그 구성을 한 번 수집하고 기록·알림 뒤 끝난다(Windows 예약 작업이 띄운다). 종료 코드는 끝 0, 아니면 1."""
import sys

from core import drivers, settings

moved_failed = settings.migrate()  # 예전 이름(ibis_search)의 설정 폴더를 새 폴더로 옮긴다. 설정을 처음 읽기 전, 단일 실행 잠금 전, 드라이버 폴더(그 안)를 쓰기 전에.
if moved_failed and sys.argv[1:2] not in (["--run"], ["--browser"], ["--move-tasks"]):  # 옮기지 못한 이유를 알리고 새 폴더로 계속한다(사용자가 빈 앱만 보고 영문을 모르지 않게). 다음 시작 때 다시 옮기려 한다. 예약 실행·수집 창은 창이 멈추므로 새 폴더의 실패 표식 기록만 한다.
    import ctypes
    ctypes.windll.user32.MessageBoxW(None, moved_failed, "Data Loupe", 0x30)
drivers.use_installed()  # exe: 따로 설치한 드라이버 폴더를 백엔드를 불러오기 전에 import 경로에 더한다.

if sys.argv[1:2] == ["--browser"]:
    from collect import collect_browser

    collect_browser.run_child()
    sys.exit()

if sys.argv[1:2] == ["--move-tasks"]:  # 설치 프로그램이 끝날 때 부른다: 창·서버 없이 예전 이름(\ibis_search\)의 예약 작업을 새 폴더·새 exe로 옮기고 끝난다. 못 옮겨도 조용히 끝낸다(표식이 없으니 앱을 열 때 다시 한다).
    import lazy  # noqa: F401  데이터 라이브러리를 처음 쓸 때 올린다(다른 프로젝트 모듈보다 먼저).
    from integrations import schedule

    try:
        schedule.move_old_tasks()
    except Exception:
        pass
    sys.exit()

if sys.argv[1:2] == ["--run"]:
    import lazy  # noqa: F401  데이터 라이브러리를 처음 쓸 때 올린다(다른 프로젝트 모듈보다 먼저).
    from integrations import schedule

    sys.exit(schedule.run_alone(sys.argv[2] if len(sys.argv) > 2 else ""))

import threading

import lazy  # noqa: F401  데이터 라이브러리를 처음 쓸 때 올린다(다른 프로젝트 모듈보다 먼저).
import webview
from werkzeug.serving import make_server

# 시작할 때 올리는 것은 core/와 각 패키지의 라우트 파일(주소 등록)뿐이다. 기능 모듈은 처음 쓸 때(또는 화면을 열 때 뒤에서) 올라온다.
from collect import collect_app  # noqa: F401  수집 라우트가 앱에 달린다.
from core import draft_app  # noqa: F401  화면 임시 저장(초안) 라우트가 앱에 달린다.
from core import scratch
from core import server
from dashboard import dash_app  # noqa: F401  대시보드 라우트가 앱에 달린다.
from integrations import bot_app
from integrations import integrations_app  # noqa: F401  연동 라우트가 앱에 달린다.
from integrations import notify  # noqa: F401  보내기 라우트가 앱에 달린다.
from integrations import schedule  # 프로그램 안 예약 스레드를 띄워야 하므로 올린다(수집·알림은 예약을 돌릴 때 올라온다).
from integrations import schedule_app  # noqa: F401  예약 라우트가 앱에 달린다.
from metrics import catalog_app  # noqa: F401  지표 사전 라우트가 앱에 달린다.
from prep import prep_app  # noqa: F401  전처리 라우트가 앱에 달린다.
from query import flow_app  # noqa: F401  흐름·문장·내 지표 라우트가 앱에 달린다.
from query.search_app import app

from core import filelock

APP_LOCK = filelock.lock_path("app.lock")  # 단일 실행: 두 번째 실행은 임시 폴더 정리(아래 sweep)로 첫 실행의 작업 폴더를 지우기 전에 막는다
if not filelock.hold(APP_LOCK):
    import ctypes
    ctypes.windll.user32.MessageBoxW(None, "이미 실행 중입니다.", "Data Loupe", 0x40)
    sys.exit(1)

scratch.sweep()  # 지난번에 강제 종료돼 남은 임시 폴더를 지운다.
http = make_server("127.0.0.1", 0, app, threaded=True)  # 0: 비어 있는 포트를 자동으로 고른다. threaded: 조회 중에도 메모리·CPU 요청에 답한다.
threading.Thread(target=http.serve_forever, daemon=True).start()

webview.settings["ALLOW_DOWNLOADS"] = True  # 결과·차트 내보내기
# 열쇠를 아는 이 창만 화면을 열 수 있다(브라우저로 주소만 열면 거부된다).
MIN_SIZE = (1024, 640)  # 이보다 작게는 창을 줄이지 못한다. 화면은 창 크기에 따라 배치가 바뀐다(common.css).


def first_size():
    """첫 창 크기: 주 모니터의 85%(MIN_SIZE 이상). 모니터를 알 수 없으면 1400×900."""
    try:
        screen = webview.screens[0]
        return max(MIN_SIZE[0], int(screen.width * 0.85)), max(MIN_SIZE[1], int(screen.height * 0.85))
    except Exception:
        return 1400, 900


width, height = first_size()
window = webview.create_window("Data Loupe", f"http://127.0.0.1:{http.server_port}/?key={server.KEY}", width=width, height=height, min_size=MIN_SIZE)


def ask_save(name, file_types):
    """저장 대화상자. file_types는 고를 형식들(예: ("Parquet (*.parquet)",)). 고른 경로 또는 취소하면 None. 덮어쓰기는 대화상자가 묻는다."""
    chosen = window.create_file_dialog(webview.FileDialog.SAVE, save_filename=name, file_types=file_types)
    return chosen[0] if chosen else None


def ask_folder():
    """폴더 고르기 대화상자. 고른 폴더 또는 취소하면 None."""
    chosen = window.create_file_dialog(webview.FileDialog.FOLDER)
    return chosen[0] if chosen else None


def closing():
    """수집 중에 창을 닫으면 묻는다. False를 돌려주면 닫기가 취소된다(pywebview의 closing 이벤트). 닫으면 수집을 멈춰 받은 데까지 남긴다."""
    collect_job = lazy.loaded("collect.collect_job")  # 올라와 있지 않으면 수집이 돈 적이 없다
    running = getattr(collect_job, "running", None)  # 반쯤 올라온 모듈이면 아직 돈 적이 없다
    if running is None or not running():
        return True
    if not window.create_confirmation_dialog("수집 중", "수집이 아직 돌고 있습니다. 닫으면 받은 데까지 남기고 멈춥니다(다시 시작하면 이어 받습니다). 닫을까요?"):
        return False
    collect_job.stop()
    return True


server.ask_save = ask_save
server.ask_folder = ask_folder
window.events.closing += closing
threading.Thread(target=schedule.move_old_tasks, daemon=True).start()  # 예전 이름(\ibis_search\ 폴더)으로 등록한 Windows 작업을 새 폴더로 옮긴다(작업 스케줄러가 느려도 창을 막지 않게 뒤에서).
schedule.start_watch()  # 프로그램 안 예약: 켜져 있는 동안 때가 된 예약을 돌린다.
webview.start()  # 창을 닫으면 여기서 끝나고, 서버 스레드도 함께 종료된다.
schedule.stop_watch()
bot_app.stop()  # 켜 둔 Slack 봇의 연결을 끊는다.
if hasattr(lazy.loaded("collect.collect_job"), "stop"):  # 반쯤 올라온 모듈이면 건너뛴다
    lazy.loaded("collect.collect_job").stop(wait=10)  # 수집 작업이 받은 데까지 기록하고 끝나기를 기다린다.
if hasattr(lazy.loaded("collect.collect_browser"), "stop_child"):
    lazy.loaded("collect.collect_browser").stop_child()  # 수집용 창 프로세스를 끝낸다(남아 있어도 메인과의 연결이 끊기면 스스로 끝난다).
server.release_connection()  # 열어 둔 연결을 닫아 파일을 놓고 임시 폴더를 지운다(도는 쿼리가 있으면 취소하고 5초까지만 기다린다).
filelock.let_go(APP_LOCK)
