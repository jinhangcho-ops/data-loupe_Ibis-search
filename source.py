"""데이터 연결. 폴더의 파일을 열거나, 연결 주소로 DB에 접속한다."""
import functools
import gc
import os
import queue
import re
import threading
from pathlib import Path
from urllib.parse import quote_plus, unquote, urlsplit

from lazy import ibis

import drivers
import scratch

STOP_WAIT = 2  # 멈추라고 알린 일이 실제로 끝나기를 기다리는 시간(초).
FLOOR = "64MB"  # release가 캐시를 비울 때 잠깐 낮추는 상한. 너무 낮으면(1MB) 되돌리는 문장조차 메모리가 모자라 실패했다.

# 연결을 쓰는 일은 모두 스레드 하나(worker)에서 차례로 돌린다. 만든 스레드에서만 쓸 수 있는 연결(SQLite 등)이 있어서다.
worker, jobs = None, None


def start_worker():
    """일감 줄과 그 줄의 일을 차례로 돌리는 스레드를 새로 만든다. 줄에 None이 오면 그 스레드는 끝난다."""
    global worker, jobs
    jobs = todo = queue.SimpleQueue()

    def loop():
        for job in iter(todo.get, None):
            job()

    worker = threading.Thread(target=loop, daemon=True)  # daemon: 일이 도는 중에 프로그램을 꺼도 붙잡지 않는다.
    worker.start()


start_worker()


def within(seconds, work, late, wake=None, drop=lambda value: None, stop=lambda: False):
    """work()를 연결 스레드에서 돌려 seconds초까지만 기다린다(None이면 끝까지). DB 종류와 상관없이 쓸 수 있게 드라이버 설정 대신 이렇게 한다.
    넘기면 late 문구로 TimeoutError, 기다리는 중에 밖에서 wake를 켜면 InterruptedError를 낸다.
    그만둘 때 stop()을 부르고, 멈추라고 알렸으면(True) 일이 끝나기를 STOP_WAIT초까지 기다린다. 그래도 안 끝나거나 알릴 수 없으면
    그 스레드는 하던 일에 두고 새 스레드로 넘어가 다음 일이 막히지 않게 한다. 포기한 뒤에 늦게 나온 결과는 drop에 넘긴다(연결이면 닫는다)."""
    if threading.current_thread() is worker:  # 연결 스레드 안에서 부른 일은 그 자리에서 돌린다.
        return work()
    found, gave_up, ended, lock, wake = {}, threading.Event(), threading.Event(), threading.Lock(), wake or threading.Event()

    def run():
        try:
            value = {"value": work()}
        except Exception as e:
            value = {"error": e}
        with lock:  # 포기와 끝이 겹쳐도 결과는 돌려주거나 버리거나 둘 중 하나만 한다.
            if not gave_up.is_set():
                found.update(value)
            elif "value" in value:
                try:
                    drop(value["value"])
                except Exception:
                    pass
        ended.set()
        wake.set()

    jobs.put(run)
    timed_out = not wake.wait(seconds)
    with lock:
        if not found:
            gave_up.set()
    if gave_up.is_set():
        if not (stop() and ended.wait(STOP_WAIT)):
            jobs.put(None)
            start_worker()
        raise TimeoutError(late) if timed_out else InterruptedError()
    if "error" in found:
        raise found["error"]
    return found["value"]


def on_worker(f):
    """f를 연결 스레드에서 돌리게 한다. 연결을 만들고 쓰고 닫는 함수에 붙인다."""
    return functools.wraps(f)(lambda *args, **kwargs: within(None, lambda: f(*args, **kwargs), ""))


# 열 수 있는 파일의 확장자 → 종류. duckdb는 파일 하나를 DB로 열고, 나머지는 파일마다 테이블 하나(Polars)로 연다.
FILE_KINDS = {".duckdb": "duckdb", ".parquet": "parquet", ".csv": "csv", ".json": "json", ".jsonl": "json", ".ndjson": "json", ".xlsx": "excel"}
KINDS = ("duckdb", "parquet", "csv", "json", "excel")


def kind_of(path):
    return FILE_KINDS.get(Path(path).suffix.lower())


def find_files(folder, kinds=KINDS, deep=True):
    """폴더에서 고른 종류(kinds)의 파일을 찾아 (DuckDB 파일들, 테이블 파일들)로. 확장자는 대소문자를 가리지 않는다.
    deep이면 하위 폴더까지 찾고, 이름이 .으로 시작하는 폴더(.venv, .git 등)는 들어가지 않는다. folder가 파일이면 그 파일 하나(종류가 맞을 때)."""
    root = Path(folder)
    if root.is_file():
        files = [root]
    elif deep:
        files = []
        for top, dirs, names in os.walk(root):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            files += [Path(top, name) for name in names]
    else:
        files = [p for p in root.iterdir() if p.is_file()] if root.is_dir() else []
    picked = sorted(p for p in files if kind_of(p) in kinds)
    return [p for p in picked if kind_of(p) == "duckdb"], [p for p in picked if kind_of(p) != "duckdb"]


@on_worker
def open_duckdb(path, read_only=True, memory=None):
    """DuckDB 파일을 연다. memory(MB, 정수)를 주면 작업 메모리 상한을 걸고 넘치는 것은 임시 폴더에 쓴다
    (그 폴더는 close가 지운다). None이면 제한 없이(DuckDB 기본) 연다."""
    if not memory:
        con = ibis.duckdb.connect(path, read_only=read_only)
    else:
        folder = scratch.make()
        try:
            con = ibis.duckdb.connect(path, read_only=read_only, temp_directory=folder, memory_limit=f"{memory}MB")
        except Exception:
            scratch.remove(folder)
            raise
        con.scratch = folder
    # 이 PC에서 계산하는 연결. release가 캐시를 비운 뒤 상한을 되돌리는 문장. 기본 상한은 연 때의 값을 읽어 둔다(reset은 환경에 따라 되돌리지 못했다).
    limit = f"{memory}MB" if memory else con.con.execute("select current_setting('memory_limit')").fetchone()[0]
    con.local = f"set memory_limit='{limit}'"
    return con


def release(con):
    """쿼리가 끝난 뒤 DB가 캐시로 쥔 데이터를 비운다. 결과는 이미 받아 갔고, 다음 쿼리는 필요한 만큼 다시 읽는다.
    작업 메모리 상한을 잠깐 FLOOR로 낮췄다가 원래 값으로 되돌리는 방식이다. 연결 스레드에서 부른다.
    이 PC에서 계산하는 파일 연결만 한다. 주소로 접속한 DB·DW는 서버가 계산하므로 하지 않는다(쿼리를 더 보내지 않는다)."""
    restore = getattr(con, "local", None)
    if not restore:
        return
    try:
        con.con.execute(f"set memory_limit='{FLOOR}'")
    except Exception:  # 쥔 것이 많아 낮추지 못하면 이번에는 비우지 않는다(쿼리 결과에는 영향이 없게).
        pass
    con.con.execute(restore)  # 보이는 값("1.3 GiB")은 반올림돼 있어, 연 때의 값으로 되돌린다.


def table_names(paths):
    """파일별 테이블 이름. 보통은 파일 이름이고, 이름이 겹치는 파일만 '폴더_이름_확장자'로 한다
    (예: sub_orders_csv). 그래도 겹치면 _2, _3을 붙인다. 대소문자는 가리지 않는다."""
    stems = [p.stem.lower() for p in paths]
    root = os.path.commonpath([p.parent for p in paths]) if paths else ""
    names, used = [], set()
    for p in paths:
        name = p.stem
        if stems.count(name.lower()) > 1:
            name = "_".join(p.relative_to(root).with_suffix("").parts) + "_" + p.suffix[1:].lower()
        base, n = name, 1
        while name.lower() in used:
            n += 1
            name = f"{base}_{n}"
        used.add(name.lower())
        names.append(name)
    return names


@on_worker
def open_flat_files(paths):
    """Parquet·CSV·JSON·Excel 파일을 Polars 백엔드에 table_names의 이름으로 등록한다.
    Parquet·CSV·줄마다 객체인 JSON은 필요할 때 읽고(LazyFrame), 배열 JSON과 Excel(첫 시트)은 열 때 읽어 메모리에 둔다."""
    import polars as pl
    con = ibis.polars.connect()
    for p, name in zip(paths, table_names(paths)):
        kind = kind_of(p)
        if kind == "parquet":
            con.read_parquet(p, table_name=name)
        elif kind == "csv":
            con.read_csv(p, table_name=name)
        elif kind == "excel":
            con.create_table(name, pl.read_excel(p))
        else:
            try:
                pl.scan_ndjson(p).head(1).collect()  # 줄마다 객체 하나인 JSON
                con.read_json(p, table_name=name)
            except Exception:
                con.create_table(name, pl.read_json(p))  # [{…}, …] 배열 JSON
    return con


CONNECT_TIMEOUT = 60  # 주소로 접속할 때 기다리는 시간(초). 틀린 주소에서 드라이버가 몇 분씩 붙잡는 것을 막는다.


def open_url(url, user="", password=""):
    """duckdb://, mssql://, postgres:// 등 Ibis가 지원하는 주소로 접속한다.
    드라이버를 먼저 확인하고, 아이디·비밀번호는 입력한 것만 주소에 넣는다(비밀번호 특수문자는 인코딩)."""
    extra = drivers.prepare(url, password)
    if user or password:
        parts = urlsplit(url)
        login = user + (":" + quote_plus(password, safe="") if password else "")
        url = parts._replace(netloc=f"{login}@{parts.netloc}").geturl()
    late = f"{CONNECT_TIMEOUT}초 안에 연결되지 않았습니다. 주소와 네트워크를 확인하세요."
    return within(CONNECT_TIMEOUT, lambda: ibis.connect(url, **extra), late, drop=close)


@on_worker
def close(con):
    """연결을 닫아 파일·접속을 놓는다. 닫기를 지원하지 않는 백엔드는 그냥 둔다. 닫은 연결이 쥐던 메모리는 바로 돌려준다."""
    try:
        con.disconnect()
    except Exception:
        pass
    if hasattr(con, "scratch"):  # open_duckdb가 만든 임시 폴더
        scratch.remove(con.scratch)
    gc.collect()


def unsupported_errors():
    """연결(백엔드)이 하지 못하는 계산에서 Ibis가 내는 오류들."""
    return ibis.common.exceptions.OperationNotDefinedError, ibis.common.exceptions.UnsupportedOperationError


def interrupt(con):
    """연결이 돌리고 있는 작업을 멈추라고 드라이버에 알린다. 드라이버 연결(con.con)에 interrupt나 cancel이 있을 때만, 되는 만큼만 한다.
    알렸으면 True. 다른 스레드에서 부르는 것이라 연결 스레드로 보내지 않는다."""
    raw = getattr(con, "con", None)
    stop = getattr(raw, "interrupt", None) or getattr(raw, "cancel", None)
    try:
        if stop:
            stop()
            return True
    except Exception:
        pass
    return False


@on_worker
def list_schemas(con):
    """스키마 목록. 지원하지 않는 백엔드(Polars 등)는 빈 목록."""
    return sorted(con.list_databases()) if hasattr(con, "list_databases") else []


@on_worker
def list_tables(con, schema=None):
    return con.list_tables(database=schema) if schema else con.list_tables()


@on_worker
def get_table(con, name, schema=None):
    return con.table(name, database=schema) if schema else con.table(name)


def passwords(info):
    """연결 요청에서 가릴 비밀번호들: 비밀번호 칸의 값, 주소 안에 쓴 값(쓴 그대로와 인코딩을 푼 값)."""
    inside = urlsplit(info.get("url", "")).password or ""
    return [info.get("password", ""), inside, unquote(inside)]


def recent(info):
    """연결 요청에서 다음 실행 때 칸을 채워 줄 값만. 비밀번호는 칸의 값도, 주소 안에 쓴 값도 남기지 않는다."""
    if info["kind"] == "folder":
        return {"kind": "folder", "path": info.get("path", "")}
    parts = urlsplit(info["url"])
    login, at, host = parts.netloc.rpartition("@")
    url = parts._replace(netloc=login.partition(":")[0] + at + host).geturl() if parts.password else info["url"]
    return {"kind": "url", "url": url, "user": info.get("user", "")}


def mask(message, passwords, mark="***"):
    """메시지에서 비밀번호(원래 값과 주소에 넣은 인코딩 값)를 mark(기본 ***)로 가린다.
    4자보다 짧은 비밀번호는 다른 글자까지 가리지 않게 주소의 비밀번호 자리(:와 @ 사이)에서만 가린다."""
    for password in passwords:
        for secret in (quote_plus(password, safe=""), password) if password else ():
            if len(password) < 4:
                message = re.sub("(?<=:)" + re.escape(secret) + "(?=@)", mark, message)
            else:
                message = message.replace(secret, mark)
    return message
