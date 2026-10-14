"""검색과 전처리가 함께 쓰는 Flask 앱 하나와 공통 부분(연결, 설정, 열쇠, 화면 내려주기). app.py·prep_app.py가 여기에 자기 라우트를 단다. 따로 실행하지 않는다."""
import json
import re
import secrets
import threading
from pathlib import Path

from lazy import ibis
from flask import Flask, Response, abort, current_app, g, jsonify, request
from werkzeug.exceptions import HTTPException

import monitor
import result
import scratch
import settings
import source

app = Flask(__name__, static_folder="static")

# 연결은 메모리에만 둔다. passwords는 오류 문구에서 가릴 값, opened는 다시 열 때 쓸 요청 값, rows는 세어 둔 테이블 행 수, chars는 연결의 length()가 글자 수를 세는지(모르면 None), cancel은 지금 도는 데이터 작업의 취소 신호.
state = {"con": None, "passwords": [], "opened": None, "rows": {}, "chars": None, "cancel": threading.Event()}
turn = threading.Lock()  # 연결 하나를 같이 쓰므로 요청은 한 번에 하나만 처리한다. 메모리·CPU 요청과 취소 요청만 기다리지 않는다.
KEY = secrets.token_urlsafe(32)  # 실행할 때마다 새로 만드는 열쇠. desktop.py가 창 주소에 넣는다.
ask_save = None  # 저장 위치를 묻는 함수(기본 파일 이름 → 고른 경로 또는 None). 창을 만든 desktop.py가 채운다. 창 없이 돌 때는 None.
QUERY_TIMEOUT = 1800  # 데이터 작업 하나를 기다리는 시간(초). 넘으면 멈춘다.


def to_json(df):
    """Polars DataFrame → {columns, rows}. 날짜는 ISO 문자열이 된다."""
    return {"columns": df.columns, "rows": json.loads(df.write_json())}


def get_con():
    if state["con"] is None:
        raise ValueError("먼저 연결하세요.")
    return state["con"]


def run(work, drop=lambda value: None):
    """데이터 작업을 따로 돌려 QUERY_TIMEOUT초까지, 취소 요청이 오기 전까지만 기다린다. 그만두면 DB에도 멈추라고 한다.
    그만둔 작업이 뒤에서 늦게 끝나면 결과는 버린다(치울 것이 있으면 drop으로)."""
    state["cancel"] = threading.Event()  # 작업마다 새로 만들어 지난 취소가 남지 않게 한다.
    try:
        return source.within(QUERY_TIMEOUT, work, f"{QUERY_TIMEOUT // 60}분 안에 끝나지 않아 멈췄습니다.", state["cancel"], drop,
                             lambda: source.interrupt(state["con"]))
    except (TimeoutError, InterruptedError) as e:
        raise ValueError(str(e) or "취소했습니다.") from None


@app.errorhandler(Exception)
def on_error(e):
    if isinstance(e, HTTPException):  # 404, 405, 잘못된 JSON 등은 원래 상태 코드로
        return jsonify(error=e.description), e.code
    if isinstance(e, source.unsupported_errors()):  # 연결(백엔드)이 하지 못하는 계산
        return jsonify(error=f"이 연결에서는 지원하지 않는 계산입니다. ({e})"), 400
    message = source.mask(str(e), state["passwords"])
    if "out of memory" in message.lower():
        message = "메모리가 모자랍니다. 작업 메모리 상한을 올리거나 조건을 좁히세요. " + message
    return jsonify(error=message), 400


@app.before_request
def only_window():
    """프로그램 창의 요청만 받는다. 창은 첫 주소의 key로 들어와 쿠키를 받고, 그 뒤로는 쿠키로 확인한다."""
    given = request.args.get("key") or request.cookies.get("key", "")
    if not secrets.compare_digest(given.encode(), KEY.encode()):
        abort(403, "프로그램 창에서만 열 수 있습니다.")


@app.before_request
def wait_turn():
    if request.path not in ("/api/usage", "/api/cancel", "/api/send"):
        turn.acquire()
        g.turn = True


@app.teardown_request
def end_turn(error):
    if g.pop("turn", False):
        turn.release()


@app.get("/api/usage")
def usage():
    return jsonify(monitor.usage())


@app.post("/api/cancel")
def cancel():
    """지금 도는 데이터 작업을 그만두게 한다. 없으면 아무 일도 하지 않는다."""
    state["cancel"].set()
    return jsonify(ok=True)


def row_count(schema, base):
    """테이블의 전체 행 수. 테이블마다 한 번만 세고 연결이 바뀔 때까지 기억한다."""
    schema = schema or None
    if (schema, base) not in state["rows"]:
        table = source.get_table(get_con(), base, schema)
        state["rows"][schema, base] = run(lambda: result.get_count(table))
    return state["rows"][schema, base]


def length_counts_chars():
    """연결의 length()가 글자 수를 세는지(바이트 수가 아닌지). 연결마다 한 번 리터럴로 확인한다."""
    if state["chars"] is None:
        con = get_con()
        state["chars"] = run(lambda: con.execute(ibis.literal("가").length())) == 1
    return state["chars"]


@app.post("/api/rowcount")
def rowcount():
    """기준 테이블의 전체 행 수. 예상 시간을 구할 때 쓴다."""
    return jsonify(rows=row_count(request.json.get("schema"), request.json["base"]))


def download(expr, fmt, guard=True):
    """expr의 전체 행을 fmt(parquet, csv, xlsx) 파일로 임시 폴더에 만들어 조금씩 내려보내고, 다 보내면 지운다.
    guard가 False면 CSV·Excel의 수식 막기를 하지 않는다."""
    folder = scratch.make()
    path = Path(folder, "result." + fmt)

    def write():
        try:
            result.export(expr, path, guard)
        except Exception:
            scratch.remove(folder)
            raise

    run(write, lambda done: scratch.remove(folder))  # 그만둔 뒤에 늦게 다 만든 파일도 지운다.

    bom = b"\xef\xbb\xbf" if path.suffix == ".csv" else b""  # Excel이 CSV의 한글을 바로 읽도록 맨 앞에 붙인다.

    def stream():
        try:
            yield bom
            with open(path, "rb") as f:
                yield from iter(lambda: f.read(1 << 20), b"")
        finally:
            scratch.remove(folder)

    headers = {"Content-Disposition": f"attachment; filename={path.name}", "Content-Length": path.stat().st_size + len(bom)}
    return Response(stream(), mimetype="application/octet-stream", headers=headers)


@app.post("/api/settings")
def save_settings():
    settings.save(request.json)
    return jsonify(ok=True)


@app.post("/api/files")
def files():
    """폴더에서 찾은 파일. files는 DuckDB 파일(하나를 골라 연다), flat은 Parquet·CSV 파일(여러 개를 골라 연다)."""
    duckdb_files, flat_files = source.find_files(request.json["path"])
    if not duckdb_files and not flat_files:
        raise ValueError("DuckDB·Parquet·CSV 파일이 없습니다.")
    return jsonify(files=[*map(str, duckdb_files)], flat=[*map(str, flat_files)])


def open_con(info, read_only=True):
    """연결 요청 값(folder는 path와 file 또는 files, url은 url·user·password)으로 연결을 연다.
    file은 DuckDB 파일 하나, files는 고른 Parquet·CSV 파일들이다(파일마다 테이블 하나).
    DuckDB 파일은 read_only가 False일 때만 쓰기로 연다."""
    if info["kind"] == "folder":
        if "files" in info:  # 그 폴더에서 찾은 파일 중 고른 것만 연다.
            chosen = [p for p in source.find_files(info["path"])[1] if str(p) in info["files"]]
            if not chosen:
                raise ValueError("Parquet·CSV 파일을 하나 이상 고르세요.")
            return source.open_flat_files(chosen)
        if not info.get("file"):
            raise ValueError("파일을 고르세요.")
        return source.open_duckdb(info["file"], read_only, settings.load().get("memory"))
    return source.open_url(info["url"], info.get("user", ""), info.get("password", ""))


def switch(new):
    """연결을 new(연결 요청 값)로 바꾸고 스키마 목록을 돌려준다. 이전 연결을 먼저 닫고, 새 연결이 실패하면 이전 연결을 다시 열어 둔다."""
    old = state["opened"]
    passwords = source.passwords(new)
    if state["con"] is not None:  # 이전 연결을 닫아야 같은 파일을 다시 열 수 있다.
        source.close(state["con"])
        state.update(con=None, passwords=[], opened=None)
    state.update(rows={}, chars=None)
    try:
        con = open_con(new)
    except Exception as e:
        message = source.mask(str(e), passwords)  # 복원 뒤에는 이전 비밀번호로만 가려지므로 여기서 가린다.
        if old:
            try:
                state.update(con=open_con(old), passwords=source.passwords(old), opened=old)
            except Exception:
                message += " 이전 연결도 다시 열 수 없어 연결이 끊겼습니다."
        raise ValueError(message) from None
    state.update(con=con, passwords=passwords, opened=new)
    try:
        settings.write("recent.json", source.recent(new))
    except OSError:  # 적어 두지 못해도 연결은 된 것이다.
        pass
    return source.list_schemas(con)


@app.post("/api/connect")
def connect():
    body = request.json
    keys = ("path", "file", "files") if body["kind"] == "folder" else ("url", "user", "password")
    return jsonify(schemas=switch({"kind": body["kind"], **{k: body[k] for k in keys if k in body}}))


@app.get("/api/state")
def current():
    """지금 연결. 화면이 새로 열려도 연결은 서버에 남아 있으므로 칸을 채울 때 쓴다. 비밀번호는 없다."""
    if state["con"] is None:
        return jsonify(connected=False)
    opened = {**source.recent(state["opened"]), **{k: v for k, v in state["opened"].items() if k in ("file", "files")}}
    return jsonify(connected=True, opened=opened, schemas=source.list_schemas(state["con"]))


@app.get("/api/recent")
def recent():
    """마지막으로 연결에 성공한 폴더나 주소(비밀번호 없음). 없으면 null. 화면이 연결 칸을 채울 때 쓴다."""
    return jsonify(recent=settings.read("recent.json"))


@app.post("/api/disconnect")
def disconnect():
    """연결을 끊고 파일·접속을 놓는다. 연결이 없으면 아무 일도 하지 않는다."""
    if state["con"] is not None:
        source.close(state["con"])
    state.update(con=None, passwords=[], opened=None, rows={}, chars=None)
    return jsonify(ok=True)


@app.get("/api/tables")
def tables():
    return jsonify(tables=source.list_tables(get_con(), request.args.get("schema") or None))


@app.get("/")
def home():
    return page("home.html")


def page(name):
    """static 폴더의 name 파일을 화면으로 내려준다. <!--include 파일이름-->은 static의 그 파일 내용으로 바꾼다(한 번만)."""
    folder = Path(current_app.static_folder)
    html = Path(folder, name).read_text(encoding="utf-8")
    html = re.sub(r"<!--include ([A-Za-z0-9._-]+)-->", lambda m: Path(folder, m[1]).read_text(encoding="utf-8"), html)
    nonce = secrets.token_urlsafe(16)  # 응답마다 새로 만들어, 이 값이 붙은 스크립트만 실행되게 한다.
    response = Response(html.replace("<script", f'<script nonce="{nonce}"'), mimetype="text/html")
    response.headers["Content-Security-Policy"] = (
        f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'self' 'unsafe-inline'; connect-src 'self'; "
        "img-src data: blob:; base-uri 'none'; form-action 'none'")
    response.set_cookie("key", KEY, httponly=True, samesite="Strict")
    for key, value in settings.load().items():  # 저장된 설정을 쿠키로 알려 준다. 화면이 그리기 전에 읽는다.
        response.set_cookie(key, str(value), samesite="Strict")
    return response
