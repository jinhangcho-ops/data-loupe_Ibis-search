"""결과 받아 두기: 주소로 접속한 DB의 검색·SQL 결과를 한 번 받아 이 PC의 DuckDB 파일에 두고, 정렬·쪽 넘기기는 그 파일에서 한다.
DB는 정렬이 없으면(또는 정렬 값이 같은 행끼리는) 순서를 보장하지 않아 쪽마다 행이 겹치거나 빠질 수 있다. 받아 둔 결과는 받은 차례(ROW)를
마지막 정렬 키로 써서 쪽이 섞이지 않고, 쪽을 넘기거나 머리글로 정렬을 바꿔도 DB에 다시 묻지 않는다(요금이 나오는 DB도 한 번만).
결과가 상한(MAX_ROWS·MAX_BYTES)을 넘으면 받지 않는다. 그때 부르는 쪽은 지금처럼 DB에서 쪽별로 받는다.
PC 안에서 계산하는 연결(DuckDB 파일, Parquet·CSV 등 파일)은 받지 않는다. 받아 둔 파일은 scratch 임시 폴더에 두고 연결을 바꾸면 지운다.
데이터를 읽는 함수는 server.run으로 연결 스레드에서 부른다."""
import collections
from pathlib import Path

from lazy import ibis

import scratch
import settings

MAX_ROWS = 1_000_000  # 받아 둘 결과의 최대 행 수
MAX_BYTES = 500 * 1024 * 1024  # 받아 둘 결과의 최대 크기(받은 Arrow 묶음의 바이트)
CHUNK = 100_000  # 한 번에 받는 행 수
KEEP = 5  # 받아 둘 결과 수. 넘으면 오래 쓰지 않은 것부터 지운다
ROW = "__ibis_row"  # 받은 차례(0부터). 정렬의 마지막 키로 쓰고 화면에는 보이지 않는다

store = {"con": None, "folder": None, "items": collections.OrderedDict(), "made": 0}


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


def keep(key, item):
    """결과를 두고, KEEP개를 넘으면 오래 쓰지 않은 것부터 지운다."""
    items = store["items"]
    items[key] = item
    while len(items) > KEEP:
        name = items.popitem(last=False)[1][0]
        if name:
            drop(name)
    return item


def fetch(key, expr, stop, known=None):
    """expr의 결과를 CHUNK행씩 받아 이 PC의 DuckDB 테이블에 쓴다. (테이블 이름, 행 수)를 돌려준다.
    known(미리 센 행 수)이나 받는 중의 행 수·크기가 상한을 넘으면 받던 것을 지우고 (None, 아는 행 수 또는 None).
    stop이 켜지면(취소) 받던 것을 지우고 InterruptedError."""
    if known is not None and known > MAX_ROWS:
        return keep(key, (None, known))
    import pyarrow as pa
    con, name = local(), f"r{store['made']}"
    store["made"] += 1
    rows = size = 0
    try:
        with expr.to_pyarrow_batches(chunk_size=CHUNK) as reader:
            for batch in reader:
                if stop.is_set():
                    raise InterruptedError()
                size += batch.nbytes
                if rows + batch.num_rows > MAX_ROWS or size > MAX_BYTES:
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


def ensure(key, expr, stop, counter=None, fresh=False):
    """받아 둔 결과(없거나 fresh면 지금 받아서)의 (테이블 이름, 받은 행 수). counter를 주면 받기 전에 행 수를 세어 상한을 넘으면 받지 않는다.
    상한을 넘어 받지 않았으면 (None, 아는 행 수 또는 None)."""
    if fresh:
        forget(key)
    item = store["items"].get(key)
    if item is None:
        return fetch(key, expr, stop, counter() if counter else None)
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


def clear():
    """받아 둔 결과를 모두 지운다(연결을 바꾸거나 끊을 때). 연결 스레드에서 부른다."""
    store["items"].clear()
    if store["con"] is not None:
        try:
            store["con"].disconnect()
        except Exception:
            pass
        scratch.remove(store["folder"])
    store.update(con=None, folder=None)
