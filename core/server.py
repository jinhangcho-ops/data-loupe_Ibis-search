"""검색과 전처리가 함께 쓰는 Flask 앱 하나와 공통 부분(연결, 설정, 열쇠, 화면 내려주기). app.py·prep_app.py가 여기에 자기 라우트를 단다. 따로 실행하지 않는다."""
import copy
import gzip
import json
import os
import re
import secrets
import threading
from pathlib import Path

from flask import Flask, Response, abort, current_app, g, jsonify, request
from werkzeug.exceptions import HTTPException

from core import drivers
from core import features
from core import held
from core import monitor
from core import result
from core import scan_size
from core import scratch
from core import settings
from core import source
from core import sql_exec
from core import sql_funcs
from core import use_counts

app = Flask(__name__, static_folder=str(Path(__file__).resolve().parent.parent / "static"))  # 화면 파일은 프로젝트 루트의 static/(패키지별 하위 폴더)
app.json.sort_keys = False  # 구조 컬럼의 필드·행의 컬럼을 원래 차례대로 보낸다(Flask 기본은 이름 순)

# 연결은 메모리에만 둔다. passwords는 오류 문구에서 가릴 값, opened는 다시 열 때 쓸 요청 값, rows는 세어 둔 테이블 행 수, cancel은 지금 도는 데이터 작업의 취소 신호.
state = {"con": None, "passwords": [], "opened": None, "rows": {}, "names": {}, "tables": {}, "cancel": threading.Event()}
# names: 스키마 → 테이블 목록, tables: (스키마, 이름) → 테이블 식. 검색 칸을 바꿀 때마다 DB에 목록·스키마를 다시 묻지 않게 연결마다 기억한다(forget_tables로 비운다).
turn = threading.Lock()  # 연결 하나를 같이 쓰므로 요청은 한 번에 하나만 처리한다. 연결을 쓰지 않는 FREE의 경로만 기다리지 않는다.
FREE = {"/api/usage", "/api/cancel", "/api/send", "/api/drivers", "/api/funcs", "/api/features/install"}  # 차례(turn)를 기다리지 않는 경로. 연결을 쓰지 않는 라우트 모듈이 더한다.
KEY = secrets.token_urlsafe(32)  # 실행할 때마다 새로 만드는 열쇠. desktop.py가 창 주소에 넣는다.
ask_save = None  # 저장 위치를 묻는 함수(기본 파일 이름, 형식 목록 예: ("Parquet (*.parquet)",) → 고른 경로 또는 None). 창을 만든 desktop.py가 채운다. 창 없이 돌 때는 None.
ask_folder = None  # 저장할 폴더를 묻는 함수(→ 고른 폴더 또는 None). ask_save처럼 desktop.py가 채운다.
QUERY_TIMEOUT = 1800  # 데이터 작업 하나를 기다리는 시간(초). 넘으면 멈춘다.
# 화면이 서버와 같은 값을 쓰도록 page가 head.html의 SERVER에 넣는 값(설정 범위, 기본값). 라우트 모듈이 제 것을 더한다.
CONSTANTS = {"sizes": settings.SIZES, "floats": settings.FLOATS, "time": {"bands": settings.TIME_BANDS, "max": settings.MAX_TIME}, "file_kinds": source.KINDS,
             "funcs_ver": sql_funcs.version(), "agg_names": sql_funcs.agg_names(), "merge": list(sql_funcs.MERGE)}  # 함수 사전은 GET /api/funcs로 받는다(버전이 같으면 화면이 가진 것을 쓴다). 여기에는 작은 목록만
LATE = []  # (화면 폴더들, 만드는 함수): 기능 모듈의 값이 든 화면 상수. 그 화면을 내려줄 때 만들어 시작할 때 기능 모듈이 올라오지 않게 한다.


def late_constants(*pages):
    """화면(static의 폴더 이름: query·prep·dashboard·collect·integrations)을 내려줄 때 부를 함수를 단다. 함수는 상수 사본(dict)을 받아 제 값을 더한다."""
    def add(make):
        LATE.append((pages, make))
        return make
    return add


def constants(folder=None):
    """화면에 넣을 상수: CONSTANTS 사본에 그 화면(folder)용 늦은 값을 더한 것."""
    found = copy.deepcopy(CONSTANTS)
    found["features"] = features.state()  # 설치한 기능(켜지지 않은 기능은 화면이 숨긴다)
    for pages, make in LATE:
        if folder in pages:
            make(found)
    return found


def number_range(kind, low, high):
    """숫자 칸의 범위를 화면(CONSTANTS["numbers"])에 줄 모양으로."""
    return {"int": kind is int, "min": low, "max": high}


RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}  # Windows가 파일 이름으로 쓰지 못하는 이름
BAD_NAME_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')  # 파일 이름에 쓸 수 없는 글자(_로 바꾼다)


def text_wide(df, text_big):
    """이진(BLOB) 컬럼은 16진수 글자로, ±2^53을 넘는 정수가 든 컬럼은 글자로 바꾼 표(화면 JSON.parse가 정밀도를 잃는다). text_big이 거짓이면 그대로."""
    wide = [c for c in df.columns if text_big and df[c].dtype.is_integer() and max(abs(df[c].max() or 0), abs(df[c].min() or 0)) > 2**53]
    import polars as pl
    blobs = [c for c in df.columns if df[c].dtype == pl.Binary]  # write_json이 이진 값에서 패닉한다 → 16진수 글자로
    def hexed(t, big=False):  # 이진(big이면 정수도)을 글자로 바꾼 타입
        return (pl.String if t == pl.Binary or big and t.is_integer() else pl.List(hexed(t.inner, big)) if isinstance(t, pl.List) else pl.Array(hexed(t.inner, big), t.size) if isinstance(t, pl.Array)
                else pl.Struct({f.name: hexed(f.dtype, big) for f in t.fields}) if isinstance(t, pl.Struct) else t)
    def hex_of(v, big=False):
        return (v.hex() if isinstance(v, bytes) else str(v) if big and type(v) is int else [hex_of(x, big) for x in v] if isinstance(v, list)
                else {k: hex_of(x, big) for k, x in v.items()} if isinstance(v, dict) else v)
    def big_in(v):  # 목록·구조 안 어디든 ±2^53을 넘는 정수가 있는지
        return abs(v) > 2**53 if type(v) is int else any(map(big_in, v)) if isinstance(v, list) else any(map(big_in, v.values())) if isinstance(v, dict) else False
    big = {c for c in df.columns if text_big and not df[c].dtype.is_integer() and hexed(df[c].dtype, True) != hexed(df[c].dtype) and big_in(df[c].to_list())}  # 목록·구조 안의 큰 정수도 → 그 컬럼 안의 정수를 글자로
    nested = [c for c in df.columns if c not in blobs and hexed(df[c].dtype, c in big) != df[c].dtype]  # 목록·구조 안의 이진도 패닉한다 → 그 안의 이진만 16진수 글자로(펼쳐 보기는 그대로)
    return df.with_columns(*[df[c].cast(str) for c in wide], *[df[c].bin.encode("hex") for c in blobs],
                           *[pl.Series(c, [hex_of(v, c in big) for v in df[c].to_list()], dtype=hexed(df[c].dtype, c in big)) for c in nested])


def dump(df):
    """df.write_json(). Polars의 패닉(BaseException이라 Flask 오류 처리가 놓친다)은 오류 글이 있는 RuntimeError로."""
    try:
        return df.write_json()
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as e:
        raise RuntimeError(f"데이터 엔진 내부 오류로 결과를 JSON으로 만들지 못했습니다: {e}") from None


def to_json(df, text_big=True):
    """Polars DataFrame → {columns, rows}. 날짜는 ISO 문자열이 된다. ±2^53을 넘는 정수가 든 컬럼은 글자로 보낸다(화면 JSON.parse가 정밀도를 잃는다).
    text_big이 거짓이면 숫자로 둔다(서버가 이어서 산술에 쓰는 결과)."""
    df = text_wide(df, text_big)
    return {"columns": df.columns, "rows": json.loads(dump(df))}


def json_reply(df, text_big=True, **fields):
    """jsonify(rows=to_json(df, text_big), **fields)와 같은 응답을, 표를 Python 값으로 풀었다 다시 쓰지 않고 Polars가 쓴 JSON을 그대로 이어 붙여 만든다(큰 표에서 훨씬 빠르다).
    fields의 이름은 rows면 안 된다(표가 rows로 들어간다). 값은 jsonify와 같은 규칙(app.json)으로 JSON이 된다."""
    df = text_wide(df, text_big)
    head = '{"rows":{"columns":' + app.json.dumps(df.columns) + ',"rows":' + dump(df) + "}"
    tail = app.json.dumps(fields)
    return app.response_class(head + ("," + tail[1:] if fields else "}") + "\n", mimetype="application/json")


def expr_sql(con, expr):
    """(SQL 글, 방언 또는 None): 처리 용량을 셀 때 읽을 Ibis 식의 SQL. 파일 연결(Polars)은 SQL을 쓰지 않으므로 duckdb 방언으로 만든다."""
    return scan_size.sql_of(con, expr)


def scan_gate(body, make_sql, rows=None):
    """실행 전 처리 용량 확인(scan_size.limit_check)을 서버에서도 한다(화면을 거치지 않는 요청 대비). make_sql()은 (SQL, 방언)을 돌려주는 함수, rows는 이미 아는 행 수(원격 DB에서 COUNT를 아끼려고).
    원격 DB(held.remote)는 DB가 읽는 양이 아니라 이 PC로 받는 양을 견준다(scan_size.limit_check). 받지 않는 일(파일로 흘려 쓰기, DB 안의 계산)은 부르는 쪽이 이 함수를 부르지 않는다.
    기준을 넘는데 body에 confirmed가 true가 아니면, 알 수 없는데 설정 scan_unknown이 block이면(확인해도) 실행하지 않고 돌려줄 응답
    {needs_confirm, blocked, text, bytes, limit, …}(머리 X-Needs-Confirm)을 돌려준다. 그 밖에는 None(그대로 실행)."""
    config = settings.load()
    confirmed = body.get("confirmed") is True
    if confirmed and not scan_size.blocks_unknown(config):
        return None
    con, opened = get_con(), state["opened"]

    def work():
        try:
            sql, dialect = make_sql()
        except Exception as e:
            return {"over": False, "unknown": True, "blocked": scan_size.blocks_unknown(config), "bytes": None, "limit": None,
                    "text": "처리 용량을 미리 알 수 없어요(" + scan_size.reason(e) + ")."}
        return scan_size.limit_check(con, sql, opened, dialect, config, rows)

    found = run(work)
    if not (found["blocked"] or (found["over"] and not confirmed)):
        return None
    response = jsonify(needs_confirm=True, **found)
    response.headers["X-Needs-Confirm"] = "1"
    return response


def get_con():
    if state["con"] is None:
        raise ValueError("먼저 연결하세요.")
    return state["con"]


def run(work, drop=lambda value: None):
    """데이터 작업을 따로 돌려 QUERY_TIMEOUT초까지, 취소 요청이 오기 전까지만 기다린다. 그만두면 DB에도 멈추라고 한다.
    그만둔 작업이 뒤에서 늦게 끝나면 결과는 버린다(치울 것이 있으면 drop으로)."""
    state["cancel"] = threading.Event()  # 작업마다 새로 만들어 지난 취소가 남지 않게 한다.
    con = state["con"]

    def then_release():  # 끝나면 결과만 남기고 쿼리하며 올린 데이터는 비운다(그만둔 작업도 끝난 뒤에).
        try:
            return work()
        finally:
            source.release(con)

    try:
        return source.within(QUERY_TIMEOUT, then_release, f"{QUERY_TIMEOUT // 60}분 안에 끝나지 않아 멈췄습니다.", state["cancel"], drop,
                             lambda: source.interrupt(state["con"]))
    except (TimeoutError, InterruptedError) as e:
        raise ValueError(str(e) or "취소했습니다.") from None


def run_writing(write, clean, runner=None):
    """파일을 쓰는 write()를 run(또는 runner)으로 돌린다. 쓰다 실패하면 clean()으로 치우고, 그만둔 뒤에 늦게 다 쓴 것도 치운다."""
    def guarded():
        try:
            return write()
        except Exception:
            clean()
            raise

    return (runner or run)(guarded, lambda done: clean())


@app.errorhandler(Exception)
def on_error(e):
    if isinstance(e, HTTPException):  # 404, 405, 잘못된 JSON 등은 원래 상태 코드로
        return jsonify(error=e.description), e.code
    if isinstance(e, source.unsupported_errors()):  # 연결(백엔드)이 하지 못하는 계산
        return jsonify(error=f"이 연결에서는 지원하지 않는 계산입니다. ({e})"), 400
    message = source.mask(str(e), state["passwords"])
    if isinstance(e, drivers.DriverError):  # 드라이버가 없어서 못 붙음: 화면이 설명·명령을 그 자리에 그리도록 안내를 함께 보낸다.
        return jsonify(error=message, driver=e.driver), 400
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
    if request.path not in FREE:
        turn.acquire()
        g.turn = True


@app.teardown_request
def end_turn(error):
    if g.pop("turn", False):
        turn.release()


GZIP_FROM = 16 * 1024  # 이보다 큰 응답은 gzip으로 보낸다. 로컬(127.0.0.1) 통신을 검사하는 보안 프로그램이 큰 응답을 가끔 19초 뒤 끊는다(측정: 60KB는 안 끊기고 200KB는 30번에 4번)


PACKED_TYPES = {"application/zip", "application/gzip", "application/x-gzip", "application/x-zip-compressed", "application/vnd.apache.parquet", "application/x-parquet", "application/pdf"}  # 이미 압축된(또는 줄어들지 않는) 형식


@app.after_request
def squeeze(response):
    """큰 응답(JSON 등)을 gzip으로 줄여 보낸다. 파일 내려주기(direct_passthrough)·스트림(내보내기는 길이를 알아도 스트림이라 통째로 메모리에 올리지 않는다)·이미 압축한 것(이미지·zip·parquet 등)은 그대로."""
    if (response.status_code != 200 or response.direct_passthrough or response.is_streamed or response.content_length is None or response.content_length < GZIP_FROM
            or response.headers.get("Content-Encoding")
            or response.mimetype.startswith(("image/", "video/", "audio/")) or response.mimetype in PACKED_TYPES or not request.accept_encodings["gzip"]):
        return response
    response.set_data(gzip.compress(response.get_data(), 5))
    response.headers["Content-Encoding"] = "gzip"
    response.vary.add("Accept-Encoding")
    return response


@app.get("/api/usage")
def usage():
    return jsonify(monitor.usage())


@app.post("/api/cancel")
def cancel():
    """지금 도는 데이터 작업을 그만두게 한다. 없으면 아무 일도 하지 않는다."""
    state["cancel"].set()
    return jsonify(ok=True)


def connection_key():
    """지금 연결을 알아보는 열쇠(비밀번호 없음). 가상 테이블을 연결마다 따로 둘 때 쓴다."""
    if state["opened"] is None:
        raise ValueError("먼저 연결하세요.")
    return use_counts.connection_key(opened_info())


def opened_info():
    """지금 연결의 알림용 정보(비밀번호 없음): source.recent에 연 파일(file·files)을 더한 것."""
    return {**source.recent(state["opened"]), **{k: v for k, v in state["opened"].items() if k in ("file", "files")}}


def virtual_tables():
    """지금 연결의 가상 테이블 {이름: 정의}. 연결이 없으면 빈 것."""
    return sql_exec.virtual(connection_key()) if state["opened"] is not None else {}


@source.on_worker
def forget_tables():
    """기억한 테이블 목록·스키마를 비운다(연결을 바꾸거나 다시 열 때, 테이블을 만들 때, 화면이 목록을 새로 받을 때)."""
    state.update(names={}, tables={})
    SUPPORT.clear()  # 함수 지원 시험도 테이블이 바뀌었을 수 있다


def release_connection(wait=5):
    """프로그램을 끝낼 때 열어 둔 연결을 놓는다: 도는 작업에 취소를 알리고(cancel·interrupt) 닫고 받아 둔 결과(held)를 지운다. 도는 쿼리 때문에 닫기가 끝나지 않을 수 있어 wait초까지만 기다린다."""
    con = state["con"]
    if con is None:
        return
    state["cancel"].set()
    source.interrupt(con)
    closer = threading.Thread(target=lambda: (source.close(con), source.on_worker(held.clear)()), daemon=True)  # 닫은 뒤 받아 둔 결과(임시 폴더)도 지운다
    closer.start()
    closer.join(wait)


def table_names(con, schema=None):
    """스키마(없으면 기본)의 테이블 목록. 연결마다 한 번 묻고 기억한다."""
    schema = schema or None
    if schema not in state["names"]:
        state["names"][schema] = source.list_tables(con, schema)
    return state["names"][schema]


def any_table(con, name, schema=None, vts=None):
    """테이블 식. 스키마를 고르지 않았고 그 이름이 가상 테이블이면 가상 테이블(sql_exec), 아니면 연결의 테이블.
    연결의 테이블 식(스키마)은 한 번 받아 기억한다(식을 만들 때마다 DB에 스키마를 묻지 않게)."""
    source.refresh_csv(con)  # 원본 CSV가 바뀌었으면 다시 변환(CP949 임시본이 낡지 않게)
    vts = virtual_tables() if vts is None else vts
    if not schema and name in vts:
        return sql_exec.virtual_table(con, name, vts)
    key = (schema or None, name)
    gen = getattr(con, "csv_gen", {}).get(name, 0)  # CSV가 바뀌어 다시 등록(또는 실패 표로 교체)될 때마다 늘어난다: 옛 스키마의 식을 버린다
    if state["tables"].get(key, (None,))[0] != gen:
        state["tables"][key] = (gen, source.get_table(con, name, schema))  # 연결 스레드에서(SQLite는 만든 스레드에서만 쓸 수 있다)
    return state["tables"][key][1]


def row_count(schema, base):
    """테이블(가상 테이블 포함)의 전체 행 수. 테이블마다 한 번만 세고 연결이 바뀌거나 원본 CSV가 바뀔 때까지 기억한다.
    세어 둘 때의 CSV 변경 횟수(csv_gen)를 함께 두고, 같으면 표 식을 만들지 않고(원격 DB에 스키마를 묻지 않고) 그 수를 그대로 쓴다."""
    schema = schema or None
    con = get_con()
    source.refresh_csv(con)  # 원본 CSV가 바뀌었으면 변경 횟수가 늘어난다
    gen = dict(getattr(con, "csv_gen", {}))
    hit = state["rows"].get((schema, base))
    if hit and hit[1] == gen:
        return hit[0]
    table = any_table(con, base, schema)
    count = run(lambda: result.get_count(table))
    state["rows"][schema, base] = (count, dict(getattr(con, "csv_gen", {})))
    return count


@app.post("/api/rowcount")
def rowcount():
    """기준 테이블의 전체 행 수. 예상 시간을 구할 때 쓴다."""
    return jsonify(rows=row_count(request.json.get("schema"), request.json["base"]))


def download(expr, fmt, guard=True):
    """expr의 전체 행을 fmt(parquet, csv, xlsx) 파일로 임시 폴더에 만들어 조금씩 내려보내고, 다 보내면 지운다.
    guard가 False면 CSV·Excel의 수식 막기를 하지 않는다."""
    folder = scratch.make()
    path = Path(folder, "result." + fmt)
    from prep import prep_app  # 없는 날짜를 날짜로 바꾸다 난 DB 오류의 안내 문장은 전처리와 같은 것을 쓴다
    run_writing(lambda: result.export(expr, path, guard), lambda: scratch.remove(folder), prep_app.run)

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
    """폴더(또는 파일 하나)에서 찾은 파일. files는 DuckDB 파일(하나를 골라 연다), flat은 Parquet·CSV·JSON·Excel 파일(여러 개를 골라 연다).
    kinds(없으면 전부)는 찾을 종류, deep(없으면 true)은 하위 폴더까지 찾을지."""
    body = request.json
    kinds = body.get("kinds", list(source.KINDS))
    if not isinstance(kinds, list) or not kinds or not set(kinds) <= set(source.KINDS):
        raise ValueError(f"찾을 파일 종류를 하나 이상 고르세요({', '.join(source.KINDS)}).")
    if not body.get("path"):  # 빈 경로는 Path("")가 작업 폴더(.)가 되어 그 아래를 다 훑는다
        raise ValueError("폴더나 파일 경로를 입력하세요.")
    path = Path(body["path"])
    if not path.exists():
        raise ValueError("그런 폴더나 파일이 없습니다.")
    duckdb_files, flat_files = source.find_files(path, kinds, body.get("deep", True) is not False)
    if not duckdb_files and not flat_files:
        raise ValueError("고른 종류의 파일이 없습니다." if path.is_dir() else "열 수 있는 파일이 아니거나 고른 종류가 아닙니다.")
    return jsonify(files=[*map(str, duckdb_files)], flat=[*map(str, flat_files)])


def open_con(info, read_only=True):
    """연결 요청 값(folder는 path와 file 또는 files, url은 url·user·password)으로 연결을 연다.
    file은 DuckDB 파일 하나, files는 고른 Parquet·CSV·JSON·Excel 파일들이다(파일마다 테이블 하나).
    DuckDB 파일은 read_only가 False일 때만 쓰기로 연다."""
    if info["kind"] == "folder":
        if "files" in info:  # 고른 테이블 파일들(다시 찾지 않는다. 있는지와 종류만 본다)
            chosen = sorted(Path(f) for f in info["files"] if Path(f).is_file() and source.kind_of(f) not in (None, "duckdb"))
            if not chosen:
                raise ValueError("Parquet·CSV·JSON·Excel 파일을 하나 이상 고르세요.")
            return source.open_flat_files(chosen)
        if not info.get("file"):
            raise ValueError("파일을 고르세요.")
        found = settings.load()
        return source.open_duckdb(info["file"], read_only, found.get("memory"), found.get("threads"))
    return source.open_url(info["url"], info.get("user", ""), info.get("password", ""))


def reading(path):
    """path가 지금 연결이 읽고 있는 파일인지(대소문자 무시). 읽는 파일에는 덮어쓰지 않는다."""
    opened = state["opened"] or {}
    files = [p for p in (opened.get("file"), *opened.get("files", [])) if p]
    return os.path.normcase(os.path.abspath(path)) in [os.path.normcase(os.path.abspath(p)) for p in files]


ON_SWITCH = []  # 연결이 바뀌거나 끊길 때 부를 함수들(연결에 매인 캐시를 비우는 곳이 등록한다)


def drop_connection():
    """지금 연결을 닫고 연결에 매인 것(받아 둔 결과, 기억한 테이블, 비밀번호, 연 곳, 행 수)을 비운다. 연결이 없어도 안전하다."""
    if state["con"] is not None:
        source.close(state["con"])
    state.update(con=None, passwords=[], opened=None, rows={})
    forget_tables()
    source.on_worker(held.clear)()  # 받아 둔 결과는 이전 연결의 것
    for hook in ON_SWITCH:
        hook()


def switch(new):
    """연결을 new(연결 요청 값)로 바꾸고 스키마 목록을 돌려준다. 이전 연결을 먼저 닫고, 새 연결이 실패하면 이전 연결을 다시 열어 둔다."""
    old = state["opened"]
    passwords = source.passwords(new)
    drop_connection()  # 이전 연결을 닫아야 같은 파일을 다시 열 수 있다.
    try:
        con = open_con(new)
    except Exception as e:
        message = source.mask(str(e), passwords)  # 복원 뒤에는 이전 비밀번호로만 가려지므로 여기서 가린다.
        if old:
            try:
                state.update(con=open_con(old), passwords=source.passwords(old), opened=old)
            except Exception:
                message += " 이전 연결도 다시 열 수 없어 연결이 끊겼습니다."
        if isinstance(e, drivers.DriverError):
            raise drivers.DriverError(message, e.driver) from None
        raise ValueError(message) from None
    state.update(con=con, passwords=passwords, opened=new)
    info = source.recent(new)  # 비밀번호 없는 칸 값
    try:
        use_counts.use("connections", use_counts.connection_key(info), info)
    except OSError:  # 적어 두지 못해도 연결은 된 것이다.
        pass
    return source.list_schemas(con)


@app.post("/api/connect")
def connect():
    body = request.json
    keys = ("path", "file", "files") if body["kind"] == "folder" else ("url", "user", "password")
    info = {"kind": body["kind"], **{k: body[k] for k in keys if k in body}}
    if "url" in info:
        info["url"] = source.without_placeholder(info["url"])  # 붙여넣은 [YOUR-PASSWORD]
    return jsonify(schemas=switch(info))


@app.get("/api/drivers")
def driver_list():
    """DB·DW·DL별 드라이버 상태와 설치 안내: [{key, group, name, status(installed·missing·program_needed), explanation, command, program, address, notice, after}]. 조사만 하고 받지 않는다."""
    return jsonify(drivers=drivers.overview())


@app.get("/api/state")
def current():
    """지금 연결. 화면이 새로 열려도 연결은 서버에 남아 있으므로 칸을 채울 때 쓴다. 비밀번호는 없다."""
    if state["con"] is None:
        return jsonify(connected=False)
    return jsonify(connected=True, opened=opened_info(), schemas=source.list_schemas(state["con"]))


@app.get("/api/recent")
def recent():
    """마지막으로 연결에 성공한 폴더나 주소(비밀번호 없음). 없으면 null. 화면이 연결 칸을 채울 때 쓴다."""
    return jsonify(recent=use_counts.last_connection())


@app.get("/api/connections")
def connections():
    """연결에 성공한 폴더·주소(비밀번호 없음)를 많이 쓴 순으로: [{"info", "count", "last"}]. 화면의 최근 연결 목록."""
    return jsonify(connections=use_counts.connections())


@app.post("/api/disconnect")
def disconnect():
    """연결을 끊고 파일·접속을 놓는다. 연결이 없으면 아무 일도 하지 않는다."""
    drop_connection()
    return jsonify(ok=True)


@app.get("/api/tables")
def tables():
    """스키마(없으면 기본)의 테이블 목록. virtual=1이면 지금 연결의 가상 테이블 이름(많이 쓴 순)도 virtual로(기본 스키마일 때만)."""
    schema = request.args.get("schema") or None
    forget_tables()  # 화면이 목록을 새로 받을 때는 DB에 다시 묻는다(그사이 만든 테이블·바뀐 컬럼)
    found = table_names(get_con(), schema)
    if request.args.get("virtual") != "1":
        return jsonify(tables=found)
    names = [] if schema else [n for n in virtual_tables() if n.lower() not in {t.lower() for t in found}]
    ordered = use_counts.order("virtual_tables", [f"{connection_key()}|{n}" for n in names])
    return jsonify(tables=found, virtual=[k.rpartition("|")[2] for k in ordered])


@app.get("/api/funcs")
def funcs():
    """함수 사전 전체(sql_funcs.catalog). 연결을 쓰지 않는다. ETag가 같으면 304로 답해 화면이 받아 둔 것을 쓰게 한다."""
    tag = f'"{sql_funcs.version()}"'
    if request.headers.get("If-None-Match") == tag:
        return Response(status=304, headers={"ETag": tag})
    reply = jsonify(sql_funcs.catalog())
    reply.headers["ETag"] = tag
    reply.headers["Cache-Control"] = "private, max-age=3600"
    return reply


SUPPORT = {}  # 마지막으로 시험한 (연결, 스키마, 테이블)과 그 결과. 같은 테이블이면 다시 만들어 보지 않는다.


@app.post("/api/funcs/support")
def funcs_support():
    """지금 연결이 함수 사전의 각 함수를 하는지 {id: {status, note}} (status: ok·substitute·local·unavailable). 테이블 base의 컬럼으로 시험하고 실행하지 않는다."""
    body = request.json or {}
    con, schema, base = get_con(), body.get("schema") or None, body.get("base") or ""
    from core import parse
    parse.check_name(base, table_names(con, schema) + ([] if schema else list(virtual_tables())), "테이블")
    key = (id(con), schema, base)
    if SUPPORT.get("key") != key:
        table = run(lambda: any_table(con, base, schema))
        SUPPORT.update(key=key, found=run(lambda: sql_funcs.support(table)))
    return jsonify(support=SUPPORT["found"])


@app.post("/api/features/install")
def features_install():
    """설정 창의 [적용]: 고른 기능으로 설치 프로그램을 받아 실행한다(연결을 쓰지 않아 FREE). 앱은 곧 닫힌다."""
    return jsonify(text=features.install((request.json or {}).get("features")))


@app.get("/")
def home():
    return page("core/home.html")


OSM_TILES = "https://tile.openstreetmap.org"  # 지도 카드의 [지도 배경(OpenStreetMap)]을 켰을 때만 쓰는 타일 그림 주소. 화면 CSP에서 이 주소의 img만 허용한다


def page(name):
    """static 폴더의 name 파일을 화면으로 내려준다. <!--include 파일이름-->은 static의 그 파일 내용으로 바꾼다(한 번만).
    /*SERVER*/ 자리에는 CONSTANTS를 JSON으로 넣는다(</script>로 끝나지 않게 <는 \\u003c로)."""
    folder = Path(current_app.static_folder)
    html = Path(folder, name).read_text(encoding="utf-8")
    html = re.sub(r"<!--include ([A-Za-z0-9._/-]+)-->", lambda m: Path(folder, m[1]).read_text(encoding="utf-8"), html)
    html = html.replace("/*SERVER*/{}", json.dumps(constants(name.split("/")[0]), ensure_ascii=False).replace("<", "\\u003c"))
    nonce = secrets.token_urlsafe(16)  # 응답마다 새로 만들어, 이 값이 붙은 스크립트만 실행되게 한다.
    response = Response(html.replace("<script", f'<script nonce="{nonce}"'), mimetype="text/html")
    response.headers["Content-Security-Policy"] = (
        f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'self' 'unsafe-inline'; connect-src 'self'; "
        f"img-src data: blob: {OSM_TILES}; base-uri 'none'; form-action 'none'")
    response.set_cookie("key", KEY, httponly=True, samesite="Strict")
    for key, value in settings.load().items():  # 저장된 설정을 쿠키로 알려 준다. 화면이 그리기 전에 읽는다.
        response.set_cookie(key, str(value), samesite="Strict")
    return response
