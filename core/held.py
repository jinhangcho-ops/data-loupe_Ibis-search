"""결과 받아 두기: 주소로 접속한 DB의 검색·SQL 결과를 한 번 받아 이 PC의 DuckDB 파일에 두고, 정렬·쪽 넘기기는 그 파일에서 한다.
DB는 정렬이 없으면(또는 정렬 값이 같은 행끼리는) 순서를 보장하지 않아 쪽마다 행이 겹치거나 빠질 수 있다. 받아 둔 결과는 받은 차례(ROW)를
마지막 정렬 키로 써서 쪽이 섞이지 않고, 쪽을 넘기거나 머리글로 정렬을 바꿔도 DB에 다시 묻지 않는다(요금이 나오는 DB도 한 번만).
결과가 상한(MAX_ROWS·MAX_BYTES)을 넘으면 받지 않는다. 그때 부르는 쪽은 지금처럼 DB에서 쪽별로 받는다.
PC 안에서 계산하는 연결(DuckDB 파일, Parquet·CSV 등 파일)은 받지 않는다. 받아 둔 파일은 scratch 임시 폴더에 두고 연결을 바꾸면 지운다.
데이터를 읽는 함수는 server.run으로 연결 스레드에서 부른다."""
import collections
import contextlib
from pathlib import Path

from lazy import ibis

from core import monitor
from core import result
from core import scratch
from core import settings

MAX_ROWS = 1_000_000  # 받아 둘 결과의 최대 행 수
MAX_BYTES = 500 * 1024 * 1024  # 받아 둘 결과의 최대 크기(받은 Arrow 묶음의 바이트)
KEEP = 5  # 받아 둘 결과 수. 넘으면 오래 쓰지 않은 것부터 지운다
ROW = "__ibis_row"  # 받은 차례(0부터). 정렬의 마지막 키로 쓰고 화면에는 보이지 않는다

store = {"con": None, "folder": None, "items": collections.OrderedDict(), "made": 0, "jobs": {}}  # jobs: 서버에 둔 결과 테이블(server_page) {열쇠: (결과 테이블, 행 수)}


def remote(con):
    """받아 둘 연결인지: PC 안에서 계산하는 DuckDB·Polars(파일)가 아닌 연결."""
    return con.name not in ("duckdb", "polars")


def local():
    """받아 둔 결과를 넣는 이 PC의 DuckDB(임시 폴더의 파일). 처음 쓸 때 만든다. 계산 코어 수 설정(threads)을 따른다."""
    if store["con"] is None:
        folder = scratch.make()
        try:
            threads = settings.load().get("threads")
            store["con"] = ibis.duckdb.connect(Path(folder, "held.duckdb"), **({"threads": threads} if threads else {}))
        except Exception:
            scratch.remove(folder)
            raise
        store["folder"] = folder
    return store["con"]


def drop(name):
    try:
        local().drop_table(name, force=True)
    except Exception:  # 지우지 못해도 연결을 바꾸면 폴더째 지운다.
        pass


def forget(key):
    """그 결과를 지운다([검색 실행]·[실행]으로 새로 받을 때)."""
    item = store["items"].pop(key, None)
    if item and item[0]:
        drop(item[0])


RENEWED = [None]  # 차례를 잡은 새로고침·Slack 명령이 이미 새로 받은 사본의 열쇠들(그 밖이면 None: 새로 받지 않는다)


@contextlib.contextmanager
def renewing(done):
    """이 안에서 쓰는 사본은 처음 쓸 때 한 번만 새로 받는다(쓰지 않는 검색·전처리 사본은 그대로 둔다). done은 작업 하나가 나눠 쓰는 열쇠 집합이다:
    새로고침은 카드마다 차례를 다시 잡으므로 같은 집합을 넘긴다. 차례를 놓은 사이의 화면 요청은 새로 받지 않는다. None이면 아무것도 새로 받지 않는다."""
    RENEWED[0] = done
    try:
        yield
    finally:
        RENEWED[0] = None


def has(key):
    """받아 둔 사본을 그대로 쓸 수 있는지(받아 두었고, 지금 작업이 새로 받을 차례가 아니다). 처리 용량 확인이 읽을 표를 고를 때 쓴다."""
    return key in store["items"] and (RENEWED[0] is None or key in RENEWED[0])


def renews(key):
    """이 사본을 지금 새로 받아야 하는지(작업 중이고 이 작업이 아직 새로 받지 않았으면 True, 받은 것으로 적는다)."""
    done = RENEWED[0]
    if done is None or key in done:
        return False
    done.add(key)
    return True


def keep(key, item):
    """결과를 두고, KEEP개를 넘으면 오래 쓰지 않은 것부터 지운다."""
    items = store["items"]
    items[key] = item
    while len(items) > KEEP:
        name = items.popitem(last=False)[1][0]
        if name:
            drop(name)
    return item


def fetch(key, expr, stop, known=None, cap=True):
    """expr의 결과를 묶음씩(result.batches) 받아 이 PC의 DuckDB 테이블에 쓴다. (테이블 이름, 행 수)를 돌려준다. 상한+1행까지만 묻는다.
    known(미리 센 행 수)이나 받는 중의 행 수·크기가 상한을 넘으면 받던 것을 지우고 (None, 아는 행 수 또는 None).
    cap이 거짓이면 행 상한 없이 다 받는다(전처리의 작업 사본). 어느 쪽이든 받은 크기가 작업 메모리 상한(없으면 지금 사용 가능한 메모리, monitor.bound_bytes)을
    넘으면 곧바로 멈추고 받은 것을 지운다(cap이면 (None, None), 아니면 이유를 담은 ValueError). stop이 켜지면(취소) 받던 것을 지우고 InterruptedError."""
    if cap and known is not None and known > MAX_ROWS:
        return keep(key, (None, known))
    import pyarrow as pa
    con, name = local(), f"r{store['made']}"
    store["made"] += 1
    rows = size = 0
    memory = monitor.bound_bytes()
    try:
        for batch in result.batches(expr.limit(MAX_ROWS + 1) if cap else expr):  # 상한을 넘는지만 알면 되므로 DB도 그만큼만 보낸다
            if stop.is_set():
                raise InterruptedError()
            size += batch.nbytes
            if size > memory:  # 작업 메모리 상한을 넘기 시작하면 더 받지 않는다
                drop(name)
                if cap:
                    return keep(key, (None, None))
                raise ValueError(f"PC로 받는 양이 작업 메모리 상한(또는 사용 가능한 메모리) {memory / 1e9:.1f} GB를 넘어 받던 것을 멈추고 지웠습니다. 기간·조건·컬럼을 줄이세요.")
            if cap and (rows + batch.num_rows > MAX_ROWS or size > MAX_BYTES):
                drop(name)
                return keep(key, (None, None))
            part = pa.Table.from_batches([batch]).append_column(ROW, pa.array(range(rows, rows + batch.num_rows), pa.int64()))
            if rows or name in con.list_tables():
                con.insert(name, part)
            else:
                con.create_table(name, ibis.memtable(part))  # Arrow 표를 바로 주면 이 조합(ibis 12 + duckdb 1.5)에서 오류가 난다
            rows += batch.num_rows
        if name not in con.list_tables():  # 결과가 없으면 컬럼만 있는 빈 테이블
            con.create_table(name, schema=ibis.schema({**expr.schema(), ROW: "int64"}))
    except BaseException:
        drop(name)
        raise
    return keep(key, (name, rows))


def ensure(key, expr, stop, counter=None, fresh=False, cap=True):
    """받아 둔 결과(없거나 fresh면 지금 받아서)의 (테이블 이름, 받은 행 수). counter를 주면 받기 전에 행 수를 세어 상한을 넘으면 받지 않는다.
    상한을 넘어 받지 않았으면 (None, 아는 행 수 또는 None)."""
    if fresh or renews(key):
        forget(key)
    item = store["items"].get(key)
    if item is None:
        return fetch(key, expr, stop, counter() if counter else None, cap)
    store["items"].move_to_end(key)
    return item


def table(name):
    """받아 둔 테이블의 식."""
    return local().table(name)


def page(key, expr, shape, limit, offset, stop, counter=None, fresh=False):
    """받아 둔 결과(ensure)의 (전체 행 수, offset부터 limit행의 Polars 표). shape(받아 둔 테이블)은 계산·정렬·표시 컬럼을 건 식을 돌려주고,
    행 수는 그 식에서 센다(계산이 행 수를 바꿀 수 있어서). 상한을 넘어 받지 않았으면 (아는 행 수 또는 None, None)."""
    name, rows = ensure(key, expr, stop, counter, fresh)
    if name is None:
        return rows, None
    shaped = shape(table(name))
    return shaped.count().to_pyarrow().as_py(), shaped.limit(limit, offset=offset).to_polars()


def pager(con):
    """쿼리 결과를 서버에 둔 채 쪽마다 읽을 수 있는 연결의 클라이언트(query로 결과 테이블을 만들고 list_rows로 쪽을 읽는다, BigQuery). 없으면 None."""
    client = getattr(con, "client", None)
    return client if all(hasattr(client, a) for a in ("query", "list_rows")) else None


def server_page(key, con, expr, limit, offset, fresh=False):
    """결과가 커서 이 PC에 받아 두지 못할 때: 쿼리를 한 번만 돌려 결과를 서버의 임시 결과 테이블(BigQuery는 24시간)에 두고,
    쪽마다 그 테이블을 list_rows로 읽는다(쿼리를 다시 보내지 않아 요금이 다시 나지 않는다). (전체 행 수, Polars 표). fresh면 쿼리를 다시 돌린다."""
    import polars as pl
    client, jobs = pager(con), store["jobs"]
    if fresh:
        jobs.pop(key, None)
    if key not in jobs:
        job = client.query(str(ibis.to_sql(expr)), project=getattr(con, "billing_project", None))
        done = job.result()  # 끝난 뒤에야 결과 테이블(destination)이 정해진다
        jobs[key] = (job.destination, done.total_rows)
        while len(jobs) > KEEP:
            jobs.pop(next(iter(jobs)))
    destination, total = jobs[key]
    return total, pl.from_arrow(client.list_rows(destination, start_index=offset, max_results=limit).to_arrow())


def clear():
    """받아 둔 결과를 모두 지운다(연결을 바꾸거나 끊을 때). 연결 스레드에서 부른다."""
    store["items"].clear()
    store["jobs"].clear()
    if store["con"] is not None:
        try:
            store["con"].disconnect()
        except Exception:
            pass
        scratch.remove(store["folder"])
    store.update(con=None, folder=None)
