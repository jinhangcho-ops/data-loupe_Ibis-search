"""검색·전처리 화면을 프로그램 창으로 연다. exe의 실행 파일이자 유일한 실행 방법."""
import threading

import webview
from werkzeug.serving import make_server

import notify  # noqa: F401  보내기 라우트가 앱에 달린다.
import prep_app  # noqa: F401  전처리 라우트가 앱에 달린다.
import server
from app import KEY, app

http = make_server("127.0.0.1", 0, app, threaded=True)  # 0: 비어 있는 포트를 자동으로 고른다. threaded: 조회 중에도 메모리·CPU 요청에 답한다.
threading.Thread(target=http.serve_forever, daemon=True).start()

webview.settings["ALLOW_DOWNLOADS"] = True  # 결과·차트 내보내기
# 열쇠를 아는 이 창만 화면을 열 수 있다(브라우저로 주소만 열면 거부된다).
window = webview.create_window("Ibis 검색·전처리", f"http://127.0.0.1:{http.server_port}/?key={KEY}", width=1400, height=900)


def ask_save(name):
    """저장 대화상자. 고른 경로 또는 취소하면 None. 덮어쓰기는 대화상자가 묻는다."""
    chosen = window.create_file_dialog(webview.FileDialog.SAVE, save_filename=name, file_types=("Parquet (*.parquet)",))
    return chosen[0] if chosen else None


server.ask_save = ask_save
webview.start()  # 창을 닫으면 여기서 끝나고, 서버 스레드도 함께 종료된다.
