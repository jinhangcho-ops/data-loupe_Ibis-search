"""수집용 브라우저 창. 같은 exe를 '--browser'로 따로 띄운 프로세스(자식)가 창 하나를 열고, 메인이 보낸 명령(이동, 스크립트 실행, 보이기·숨기기, 닫기)을 처리해 결과만 돌려준다.
자식은 로그인이 남도록 전용 폴더(FOLDER)에 쿠키를 두고, 프로그램의 함수를 페이지에 내주지 않는다(js_api 없음). http·https 밖으로의 이동과 다운로드는 막는다.
메인과 자식은 127.0.0.1의 연결(multiprocessing.connection, 실행마다 새 authkey)로 JSON을 주고받는다. 주소와 열쇠는 명령줄이 아니라 환경 변수로 넘긴다.
페이지에 넣는 스크립트는 static/collect/collect_picker.js 하나다(고르기·읽기·기다리기에 쓰는 함수들). 사이트가 결과를 바꿀 수 있으므로 받은 값은 모양·길이를 다시 검사한다."""
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from multiprocessing.connection import Client, Listener
from pathlib import Path
from urllib.parse import urlsplit

from core import settings

FOLDER = settings.PATH.parent / "browser"  # 수집용 창의 쿠키·저장소. forget이 지우는 것은 이 폴더뿐이다.
TITLE = "Data Loupe 수집용 브라우저"
ENV_ADDRESS, ENV_KEY = "IBIS_BROWSER_ADDRESS", "IBIS_BROWSER_KEY"
START_TIMEOUT = 60  # 자식이 접속하기를 기다리는 시간(초)
CALL_TIMEOUT = 20  # 명령 하나의 답을 기다리는 기본 시간(초)
NAME = "__ibis" + secrets.token_hex(6)  # 페이지에 두는 고르기 상태의 이름. 실행마다 바꿔 사이트의 이름과 겹치지 않게 한다.
MAX_SELECTOR = 2000
MAX_TEXT = 200
MAX_URL = 2000  # 상태에 싣는 주소의 길이. 페이지가 주소(#뒤)를 아주 길게 만들어도 상태 확인마다 그만큼 오가지 않게 한다.
MAX_REPLY = 64 * 1024 * 1024  # 자식이 한 번에 돌려주는 답의 크기(바이트). 넘으면 오류로 바꾼다(메인의 메모리를 지킨다).
ATTR = re.compile(r"href|src|title|alt|value|data-[a-z0-9_.:-]{1,50}")  # 고른 요소에서 보여 줄 속성
BUSY = "수집이 수집용 창을 쓰는 중이에요. 끝나거나 멈춘 뒤에 하세요."
CLOSED = "수집용 창이 열려 있지 않아요. 먼저 [브라우저 열기]를 누르세요."


class Gone(Exception):
    """자식 프로세스가 끝났다(사람이 창을 닫았거나 죽었다)."""


def web_url(url):
    """http·https 주소만 받는다."""
    if not isinstance(url, str) or urlsplit(url).scheme.lower() not in ("http", "https") or not urlsplit(url).hostname:
        raise ValueError("http:// 또는 https:// 주소만 열 수 있어요.")
    return url


# ---------- 메인 쪽 ----------

child = {"proc": None, "conn": None, "gone": True, "waiting": set(), "replies": {}, "n": 0,
         "busy": False, "picking": "", "items": "", "pick": None, "seq": 0, "url": "", "title": ""}
lock = threading.RLock()  # 띄우기·닫기·busy
sending = threading.Lock()
answered = threading.Condition()


def alive():
    proc = child["proc"]
    return proc is not None and proc.poll() is None and not child["gone"]


def launch(url, show=True):
    """자식 프로세스를 띄워 url을 연다. 접속하지 못하면 그 프로세스를 끝내고 한 번 더 띄운다.
    (빌드한 exe를 처음 실행할 때 접속 확인 중에 연결이 끊기는 것을 두 번 봤다. 두 번째부터는 됐다.)"""
    for attempt in range(2):
        try:
            return start_child(url, show)
        except ConnectionError:
            if attempt:
                raise ValueError("수집용 창을 띄우지 못했어요.") from None


def start_child(url, show):
    key = secrets.token_bytes(32)
    listener = Listener(("127.0.0.1", 0), authkey=key)
    env = {**os.environ, ENV_ADDRESS: f"127.0.0.1:{listener.address[1]}", ENV_KEY: key.hex()}
    command = [sys.executable, "--browser"] if getattr(sys, "frozen", False) else [sys.executable, str(Path(__file__).resolve().parent.parent / "desktop.py"), "--browser"]
    proc = subprocess.Popen(command, env=env)
    got = []

    def accept():
        try:
            got.append(listener.accept())
        except Exception:  # 닫았거나 열쇠가 틀린 접속
            pass

    waiter = threading.Thread(target=accept, daemon=True)
    waiter.start()
    end = time.monotonic() + START_TIMEOUT
    while waiter.is_alive() and proc.poll() is None and time.monotonic() < end:
        waiter.join(0.2)
    listener.close()
    if not got:
        proc.kill()
        raise ConnectionError()
    conn = got[0]
    try:
        conn.send_bytes(json.dumps({"url": url, "show": bool(show)}).encode())
    except OSError:  # 접속은 했는데 첫 명령을 못 보냈다. 자식을 남기지 않는다.
        proc.kill()
        conn.close()
        raise ConnectionError() from None
    child.update(proc=proc, conn=conn, gone=False, replies={}, waiting=set(), picking="", items="", url=url, title="")
    threading.Thread(target=listen, args=(conn,), daemon=True).start()


def listen(conn):
    """자식의 답을 받아 기다리는 쪽에 넘긴다. 연결이 끊기면 gone."""
    try:
        while True:
            message = json.loads(conn.recv_bytes())
            with answered:
                if message.get("id") in child["waiting"]:
                    child["replies"][message["id"]] = message
                    answered.notify_all()
    except (EOFError, OSError, ValueError):
        with answered:
            if child["conn"] is conn:
                child["gone"] = True
            answered.notify_all()


def call(do, timeout=CALL_TIMEOUT, **args):
    """자식에 명령 하나를 보내고 답(value)을 기다린다. 창이 없으면 Gone, 시간을 넘기면 TimeoutError, 자식이 오류를 알리면 ValueError."""
    conn = child["conn"]
    if conn is None or not alive():
        raise Gone()
    with answered:
        child["n"] += 1
        number = child["n"]
        child["waiting"].add(number)
    try:
        with sending:
            try:
                conn.send_bytes(json.dumps({"id": number, "do": do, **args}).encode())
            except OSError:
                raise Gone() from None
        end = time.monotonic() + timeout
        with answered:
            while number not in child["replies"]:
                if child["gone"] or child["conn"] is not conn:
                    raise Gone()
                left = end - time.monotonic()
                if left <= 0:
                    raise TimeoutError("수집용 창이 답하지 않아요.")
                answered.wait(min(left, 0.5))
                if child["proc"] is not None and child["proc"].poll() is not None:
                    child["gone"] = True
            message = child["replies"].pop(number)
    finally:
        with answered:
            child["waiting"].discard(number)
            child["replies"].pop(number, None)
    if "error" in message:
        raise ValueError(message["error"])
    return message.get("value")


def picker():
    """페이지에 넣는 스크립트(collect_picker.js). 함수들을 담은 객체 하나의 식이다."""
    if "picker" not in child:
        child["picker"] = Path(__file__).resolve().parent.parent.joinpath("static", "collect", "collect_picker.js").read_text(encoding="utf-8").rstrip().rstrip(";")
    return child["picker"]


def script(method, *args):
    """collect_picker.js의 함수 method를 args(JSON)로 부르는 스크립트."""
    return f"{picker()}\n.{method}({', '.join(json.dumps(a) for a in args)})"


class Page:
    """열린 수집용 창 하나. go(주소)로 이동하고 run(함수, 인자…)으로 collect_picker.js의 함수를 부른다.
    landing은 방금 그 주소로 띄워 이미 열고 있는 주소(한 번 더 이동하지 않게)."""

    def __init__(self, landing=None):
        self.landing = landing

    def go(self, url):
        call("go", url=web_url(url))

    def run(self, method, *args, timeout=CALL_TIMEOUT):
        return call("js", timeout, script=script(method, *args))


def page():
    """지금 열린 창(작업이 쓰는 중이면 거부)."""
    if child["busy"]:
        raise ValueError(BUSY)
    if not alive():
        raise ValueError(CLOSED)
    return Page()


def open_page(url):
    """창을 띄우거나 이미 떠 있으면 그 주소로 옮긴다."""
    url = web_url(url)
    with lock:
        if child["busy"]:
            raise ValueError(BUSY)
        if alive():
            try:
                call("go", url=url)
                call("show")
                return
            except Gone:
                pass
        stop_child()
        launch(url)


def close():
    """창을 닫는다(작업이 쓰는 중이면 거부)."""
    with lock:
        if child["busy"]:
            raise ValueError(BUSY)
        stop_child()


def stop_child():
    """자식을 끝낸다. 닫기 명령에 답하지 않으면 강제로 끝낸다."""
    proc = child["proc"]
    if proc is not None and proc.poll() is None:
        try:
            call("close", 5)
        except (Gone, TimeoutError, ValueError):
            pass
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            proc.kill()
    if child["conn"] is not None:
        child["conn"].close()
    child.update(proc=None, conn=None, gone=True, picking="", items="")


@contextmanager
def session(show, url):
    """수집 작업이 창을 쓴다(그동안 busy). 창이 없으면 url로 띄운다. show가 False면 숨긴다.
    작업이 숨긴 채 띄운 창은 끝나면 닫고, 원래 있던 창은 다시 보인다."""
    with lock:
        if child["busy"]:
            raise ValueError(BUSY)
        child["busy"] = True
    launched = False
    try:
        with lock:
            if alive() and child["picking"]:
                try:
                    call("js", script=script("pick", NAME, "off", ""))
                except (Gone, TimeoutError, ValueError):
                    pass
            child["picking"] = ""
            if not alive():
                stop_child()
                launch(url, show)
                launched = True
            elif not show:
                call("hide")
        yield Page(url if launched else None)
    finally:
        try:
            if launched and not show:
                stop_child()
            elif not show and alive():
                call("show")
        except (Gone, TimeoutError, ValueError):
            pass
        child["busy"] = False


def clean_pick(raw):
    """페이지가 준 고른 결과를 검사한다. 모양이 틀리면 None. 글자는 길이를 자른다."""
    if not isinstance(raw, dict) or raw.get("target") not in ("field", "next"):
        return None
    items, selector, text, count, attrs = (raw.get(k) for k in ("items", "selector", "text", "count", "attrs"))
    if not isinstance(items, str) or not isinstance(selector, str) or len(items) > MAX_SELECTOR or len(selector) > MAX_SELECTOR:
        return None
    if type(count) is not int or not 0 <= count <= 10**7 or not isinstance(text, str) or not isinstance(attrs, dict):
        return None
    attrs = {k: v[:MAX_TEXT] for k, v in list(attrs.items())[:20] if isinstance(k, str) and ATTR.fullmatch(k) and isinstance(v, str)}
    return {"target": raw["target"], "items": items if raw["target"] == "field" else "", "count": count, "selector": selector, "text": text[:MAX_TEXT], "attrs": attrs}


def state():
    """창의 상태(약속의 모양). 고르는 중이면 페이지에 쌓인 고른 결과를 가져오고, 페이지가 바뀌어 고르기가 사라졌으면 다시 넣는다."""
    found = {"open": alive(), "url": "", "title": "", "picking": "", "pick": child["pick"], "busy": child["busy"]}
    if not found["open"]:
        return found
    try:
        info = call("js", 5, script=script("peek", NAME))
    except (Gone, TimeoutError, ValueError):
        info = None
    if isinstance(info, dict):
        child["url"] = info.get("url")[:MAX_URL] if isinstance(info.get("url"), str) else ""
        child["title"] = info.get("title")[:300] if isinstance(info.get("title"), str) else ""
        for raw in info.get("picks") if isinstance(info.get("picks"), list) else []:
            pick = clean_pick(raw)
            if pick and child["picking"]:
                child["seq"] += 1
                child["pick"] = {"seq": child["seq"], **pick}
        if child["picking"] and not info.get("on") and not child["busy"]:
            try:
                call("js", 5, script=script("pick", NAME, child["picking"], child["items"]))
            except (Gone, TimeoutError, ValueError):
                pass
    found.update(open=alive(), url=child["url"], title=child["title"], picking=child["picking"], pick=child["pick"])
    return found


def pick(target, items=""):
    """고르기를 켜거나(field·next) 끈다(off)."""
    if target not in ("field", "next", "off"):
        raise ValueError("target은 field, next, off 중 하나예요.")
    if not isinstance(items, str) or len(items) > MAX_SELECTOR:
        raise ValueError("항목 선택자가 올바르지 않아요.")
    with lock:
        page()
        try:
            call("js", script=script("pick", NAME, target, items))
        except Gone:
            raise ValueError(CLOSED) from None
        child["picking"], child["items"] = ("", "") if target == "off" else (target, items)


def forget():
    """수집용 창의 로그인 정보(FOLDER)를 지운다. 창이 떠 있거나 폴더·그 위 폴더가 링크·정션이면 거부한다."""
    with lock:
        if alive() or (child["proc"] is not None and child["proc"].poll() is None):  # 연결만 끊기고 프로세스가 도는 창도 폴더를 쥐고 있다.
            raise ValueError("수집용 창을 닫은 뒤에 지우세요.")
        for folder in (FOLDER.parent, FOLDER):
            if folder.is_symlink() or os.path.isjunction(folder):
                raise ValueError(f"{folder}가 링크라서 지우지 않아요.")
        if not FOLDER.exists():
            return
        if not FOLDER.is_dir():
            raise ValueError(f"{FOLDER}가 폴더가 아니에요.")
        failed = []
        shutil.rmtree(FOLDER, onexc=lambda function, path, error: failed.append(path))  # 안의 링크·정션은 따라가지 않고 링크만 지운다.
        if failed:
            raise ValueError("일부 파일을 지우지 못했어요. 창을 닫은 직후라면 잠시 뒤에 다시 하세요.")


# ---------- 자식 쪽 ----------

def run_child():
    """자식 프로세스(desktop.py --browser): 메인에 접속해 첫 주소로 창을 띄우고, 명령을 받아 처리한다. 메인과의 연결이 끊기거나 창을 닫으면 끝난다."""
    import webview  # 자식에서만 올린다.

    host, _, port = os.environ.pop(ENV_ADDRESS, "").rpartition(":")
    conn = Client((host, int(port)), authkey=bytes.fromhex(os.environ.pop(ENV_KEY, "")))
    first = json.loads(conn.recv_bytes())
    webview.settings["ALLOW_DOWNLOADS"] = False  # 다운로드는 취소된다.
    webview.settings["ALLOW_FILE_URLS"] = False
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = False  # 새 창·팝업은 같은 창에서 연다(기본 브라우저로 넘기지 않는다).
    window = webview.create_window(TITLE, web_url(first["url"]), width=1200, height=860, hidden=not first.get("show", True))
    window.events.before_show += lambda: guard(window)
    lock_send = threading.Lock()

    def reply(message):
        with lock_send:
            try:
                conn.send_bytes(encode(message))
            except (OSError, TypeError, ValueError):
                pass

    def handle(message):
        try:
            reply({"id": message["id"], "value": command(window, message)})
        except Exception as e:
            reply({"id": message["id"], "error": str(e) or type(e).__name__})

    def serve():
        try:
            while True:
                message = json.loads(conn.recv_bytes())
                threading.Thread(target=handle, args=(message,), daemon=True).start()  # 멈춘 스크립트가 다른 명령을 막지 않게 따로 돈다.
        except (EOFError, OSError, ValueError):  # 메인이 끝났다.
            threading.Timer(5, os._exit, (0,)).start()  # 창이 닫히지 않아도 끝낸다.
            try:
                window.destroy()
            except Exception:
                os._exit(0)

    webview.start(serve, private_mode=False, storage_path=str(FOLDER))
    conn.close()


def encode(message):
    """답을 JSON 바이트로. MAX_REPLY를 넘으면 오류 답으로 바꾼다."""
    data = json.dumps(message).encode()
    if len(data) > MAX_REPLY:
        data = json.dumps({"id": message.get("id"), "error": "페이지가 돌려준 값이 너무 커요. 항목 선택자를 좁히세요."}).encode()
    return data


def command(window, message):
    do = message.get("do")
    if do == "go":
        navigate(window, web_url(message.get("url")))
    elif do == "js":
        return window.run_js(message["script"])
    elif do == "show":
        window.show()
    elif do == "hide":
        window.hide()
    elif do == "close":
        window.destroy()
    else:
        raise ValueError(f"모르는 명령이에요: {do}")
    return True


def navigate(window, url):
    """url로 이동한다. 지금과 같은 주소여도 다시 연다(pywebview의 load_url은 엔진의 Source를 바꾸는데, 같은 주소면 이동하지 않는다)."""
    from System import Action, Uri

    def go():  # 엔진의 개체는 창의 스레드에서만 다룰 수 있다.
        control = window.native.webview
        if control.CoreWebView2 is None:  # 엔진이 아직 준비되지 않음
            control.Source = Uri(url)
        else:
            control.CoreWebView2.Navigate(url)

    window.events.shown.wait(20)
    window.native.Invoke(Action(go))


def guard(window):
    """창의 엔진(WebView2)에 막기를 단다(창을 만든 직후, 창의 스레드에서). 맨 위 문서는 http·https만, 틀(iframe)은 그 밖에 about·data·blob만.
    제목은 'TITLE — 페이지 제목'으로 따라 바꾼다."""
    control = window.native.webview

    def starting(sender, args, allowed=("http", "https")):
        if urlsplit(str(args.Uri)).scheme.lower() not in allowed:
            args.Cancel = True

    def titled(sender, args):
        title = (sender.DocumentTitle or "").strip()[:100]
        window.native.Text = f"{TITLE} — {title}" if title else TITLE

    def ready(sender, args):
        if args.IsSuccess:
            core = sender.CoreWebView2
            core.FrameNavigationStarting += lambda s, a: starting(s, a, ("http", "https", "about", "data", "blob"))
            core.DocumentTitleChanged += titled

    control.NavigationStarting += starting
    control.CoreWebView2InitializationCompleted += ready
