"""적재할 곳: DB 테이블. 검색 화면의 연결(server.state["con"])과 따로 연결을 열고, 연결을 쓰는 일은 모두 source의 연결 스레드에서 돌린다
(만든 스레드에서만 쓸 수 있는 SQLite 같은 연결 때문). 비밀번호는 인자로만 받고 어디에도 적지 않는다.
바꾸기(replace)는 이 프로그램이 만든 테이블에만 한다: 만든 테이블은 설정 폴더의 load_tables.json에 {연결, 스키마, 이름, 컬럼, 만든 때}로 남긴다."""
import datetime as dt
import os
from pathlib import Path
from urllib.parse import urlsplit

from lazy import ibis

import parse
import prep_app
import server
import settings
import source

FILE = "load_tables.json"
STAGING = "_ibis_load"  # 바꾸기: 새 내용을 먼저 넣어 둘 테이블 이름의 꼬리. 다 넣은 뒤 원래 테이블과 바꿔치기한다.
OLD = "_ibis_old"  # 바꿔치기하는 동안 원래 테이블을 잠깐 옮겨 둘 이름의 꼬리
OPENED_HERE = "검색·전처리 화면이 이 DB 파일을 열고 있습니다. 검색·전처리의 연결을 끊고 적재하세요."


# ---------- 구성 ----------

def ready(config):
    """시작 전 검사(연결 없이): 이름, 연결 칸, 검색 화면이 같은 파일을 열고 있는지."""
    table = config["load"]["table"]
    prep_app.check_table_name(table["name"])
    if table["schema"]:
        prep_app.check_table_name(table["schema"], "스키마")
    if table["key"] and table["how"] != "append":
        raise ValueError("이미 있는 키를 건너뛰는 것(key)은 덧붙이기(append)에서만 씁니다.")
    link = table["connection"]
    if link["kind"] == "folder":
        file = link["file"].strip()
        if not file or Path(file).suffix.lower() != ".duckdb" or not Path(file).parent.is_dir():
            raise ValueError("적재할 DB 파일(.duckdb)을 고르세요.")
        if server.reading(file):
            raise ValueError(OPENED_HERE)
    elif not link["url"].strip():
        raise ValueError("적재할 DB의 연결 주소를 입력하세요.")


def ident(link):
    """연결을 알아보는 글자(비밀번호 없음): 파일은 경로, 주소는 주소와 아이디."""
    if link["kind"] == "folder":
        return os.path.normcase(os.path.abspath(link["file"].strip()))
    return link["url"].strip() + (f" ({link['user']})" if link["user"] else "")


def where(config):
    """상태에 보여 줄 적재 위치: 'warehouse.duckdb · main.items'."""
    table = config["load"]["table"]
    link = table["connection"]
    place = Path(link["file"]).name if link["kind"] == "folder" else urlsplit(link["url"]).netloc + urlsplit(link["url"]).path
    return f"{place} · {table['schema'] + '.' if table['schema'] else ''}{table['name']}"


def sign(config):
    """이어 넣기를 정하는 대상: 연결, 스키마, 이름, 방식, 키."""
    table = config["load"]["table"]
    return {"connection": ident(table["connection"]), "schema": table["schema"], "name": table["name"].lower(), "how": table["how"], "key": table["key"]}


# ---------- 만든 테이블 기록 ----------

def made():
    found = settings.read(FILE) or {}
    return [t for t in found.get("tables", []) if isinstance(t, dict)] if isinstance(found.get("tables"), list) else []


def same(record, link, schema, name):
    """기록이 그 연결·스키마·테이블(이름은 대소문자 무시)의 것인지."""
    return (record.get("connection"), record.get("schema"), str(record.get("name", "")).lower()) == (ident(link), schema, name.lower())


def mine(link, schema, name):
    """이 프로그램이 만든 테이블의 기록. 없으면 None."""
    return next((t for t in made() if same(t, link, schema, name)), None)


def remember(link, schema, name, columns):
    """만든 테이블을 기록한다(같은 것이 있으면 바꾼다). columns가 None이면 기록을 지운다."""
    tables = [t for t in made() if not same(t, link, schema, name)]
    if columns is not None:
        tables.append({"connection": ident(link), "schema": schema, "name": name, "columns": columns, "created": dt.datetime.now().isoformat(timespec="seconds")})
    settings.write(FILE, {"tables": tables})


# ---------- 연결 ----------

def connect(link, password):
    """적재용 연결을 따로 연다. 파일은 쓰기로 연다(검색 화면이 같은 파일을 열고 있으면 거부)."""
    if link["kind"] == "folder":
        if server.reading(link["file"]):
            raise ValueError(OPENED_HERE)
        try:
            return source.open_duckdb(link["file"].strip(), read_only=False, memory=settings.load().get("memory"))
        except Exception as e:
            raise ValueError(f"DB 파일을 쓰기로 열 수 없습니다. 다른 프로그램이 이 파일을 열고 있는지 확인하세요. ({e})") from None
    return source.open_url(link["url"].strip(), link["user"], password)


def columns_of(schema):
    """Ibis 스키마 → [[이름, 한글 타입]] (검색 화면의 컬럼 목록과 같은 타입 이름)."""
    return [[name, parse.type_info(dtype)[0]] for name, dtype in schema.items()]


def arrow_columns(arrow_schema):
    """결과 파일(pyarrow 스키마)의 [[이름, 한글 타입]]."""
    return columns_of(ibis.Schema.from_pyarrow(arrow_schema))


def differences(file, table):
    """결과 파일과 테이블의 컬럼([[이름, 타입]])이 다른 점. 같으면 빈 글자. 순서는 보지 않는다(이름으로 넣는다)."""
    a, b = dict(map(tuple, file)), dict(map(tuple, table))
    found = []
    if only := [n for n in a if n not in b]:
        found.append(f"결과 파일에만 있는 컬럼 {', '.join(only)}")
    if only := [n for n in b if n not in a]:
        found.append(f"테이블에만 있는 컬럼 {', '.join(only)}")
    if other := [f"{n}({a[n]} ↔ {b[n]})" for n in a if n in b and a[n] != b[n]]:
        found.append(f"타입이 다른 컬럼 {', '.join(other)}")
    return " / ".join(found)


@source.on_worker
def look(con, schema, name):
    """(실제 이름, 컬럼 [[이름, 타입]]). 테이블이 없으면 (None, None). 이름은 대소문자를 가리지 않고 찾는다."""
    names = con.list_tables(database=schema or None)
    real = next((t for t in names if t.lower() == name.lower()), None)
    if real is None:
        return None, None
    return real, columns_of(con.table(real, database=schema or None).schema())


@source.on_worker
def create(con, schema, name, arrow_schema):
    con.create_table(name, schema=ibis.Schema.from_pyarrow(arrow_schema), database=schema or None)


@source.on_worker
def drop(con, schema, name):
    """테이블을 지운다. Ibis 12의 drop_table은 지금 쓰는 sqlglot(30)에서 빈 문장(DROP TABLE)을 만들어, 같은 문장을 연결의 SQL 방언으로 직접 만든다."""
    import sqlglot
    statement = sqlglot.exp.Drop(kind="TABLE", tables=[sqlglot.table(name, db=schema or None, quoted=con.compiler.quoted)])
    con.raw_sql(statement.sql(con.dialect))


@source.on_worker
def insert(con, schema, name, data):
    """묶음(pyarrow 표) 하나를 넣는다. Ibis insert 한 번 = 문장 하나라 묶음째 들어가거나 들어가지 않는다."""
    con.insert(name, ibis.memtable(data), database=schema or None)


@source.on_worker
def existing(con, schema, name, keys, data):
    """묶음의 키 중 테이블에 이미 있는 것들(튜플의 집합). 묶음의 키 값만으로 걸러 조회한다."""
    values = {k: [v for v in set(data.column(k).to_pylist()) if v is not None] for k in keys}
    if not all(values.values()):
        return set()
    table = con.table(name, database=schema or None)
    rows = table.filter([table[k].isin(values[k]) for k in keys]).select(keys).distinct().to_pyarrow().to_pylist()
    return {tuple(row[k] for k in keys) for row in rows}


@source.on_worker
def rename(con, schema, name, new):
    """테이블 이름을 바꾼다. Ibis 12의 rename_table은 DuckDB·SQLite에서 지원되지 않고 스키마도 받지 않아, ALTER TABLE … RENAME TO 문장을 연결의 SQL 방언으로 만든다."""
    import sqlglot
    quoted = con.compiler.quoted
    statement = sqlglot.exp.Alter(this=sqlglot.table(name, db=schema or None, quoted=quoted), kind="TABLE",
                                  actions=[sqlglot.exp.AlterRename(this=sqlglot.table(new, quoted=quoted))])
    con.raw_sql(statement.sql(con.dialect))


def swap(con, schema, name, staging):
    """바꾸기의 마지막: 새 내용(staging)을 원래 이름으로. 원래 것을 옆으로 옮기고 → 새 것을 그 이름으로 → 옮긴 것을 지운다(중간에 실패하면 원래 것을 되돌린다).
    이름 바꾸기 문장이 듣지 않는 연결이면 원래 것을 지우고 새 내용으로 다시 만든다."""
    db, old = schema or None, name + OLD
    if old.lower() in [t.lower() for t in con.list_tables(database=db)]:
        raise ValueError(f"바꾸는 동안 원래 테이블을 옮겨 둘 이름 '{old}'이 이미 있어 바꾸지 않았습니다. 새 내용은 '{staging}'에 남아 있습니다.")
    try:
        rename(con, schema, name, old)
    except Exception:
        drop(con, schema, name)
        con.create_table(name, con.table(staging, database=db), database=db)
        drop(con, schema, staging)
        return
    try:
        rename(con, schema, staging, name)
    except Exception:
        rename(con, schema, old, name)
        raise
    drop(con, schema, old)


# ---------- 미리 확인 ----------

def check(config, password, file_columns):
    """적재 전 확인(/api/load/table/check). file_columns는 결과 파일의 컬럼([[이름, 타입]]), 파일이 아직 없으면 None."""
    table = config["load"]["table"]
    con = connect(table["connection"], password)
    try:
        real, columns = look(con, table["schema"], table["name"])
    finally:
        source.close(con)
    ours = real is not None and mine(table["connection"], table["schema"], real) is not None
    found = {"exists": real is not None, "columns": file_columns, "table_columns": columns, "ours": ours, "ok": True}
    try:
        found["message"] = plan(table, real, columns, file_columns)
    except ValueError as e:
        found.update(ok=False, message=str(e))
    return found


def plan(table, real, columns, file_columns, resume=False):
    """방식(how)대로 넣을 수 있는지 보고 할 일을 한 줄로. 넣을 수 없으면 ValueError."""
    how, link = table["how"], table["connection"]
    missing = [k for k in table["key"] if file_columns is not None and k not in dict(map(tuple, file_columns))]
    if missing:
        raise ValueError(f"키 컬럼이 결과 파일에 없습니다: {', '.join(missing)}")
    if real is None:
        if how == "append":
            raise ValueError(f"덧붙일 테이블 '{table['name']}'이 없습니다.")
        return "새 테이블로 만듭니다."
    record = mine(link, table["schema"], real)
    if how == "new":
        if resume and record:
            return "이어 넣습니다."
        raise ValueError(f"'{real}'은 이미 있는 테이블입니다. 다른 이름을 쓰거나 덧붙이기·바꾸기를 고르세요.")
    if how == "append":
        if file_columns is None:
            return "테이블이 있습니다. 컬럼은 수집한 뒤 결과 파일과 맞춰 봅니다."
        gap = differences(file_columns, columns)
        if gap:
            raise ValueError(f"컬럼이 달라 덧붙일 수 없습니다. 다른 점: {gap}")
        return "컬럼이 같아 덧붙일 수 있습니다."
    if record is None:
        raise ValueError("이 프로그램이 만든 테이블이 아니라 바꿀 수 없습니다.")
    if differences(record["columns"], columns):
        raise ValueError("이 프로그램이 만든 뒤 테이블의 컬럼이 바뀌어 바꾸지 않습니다.")
    return "이 프로그램이 만든 테이블이라 바꿀 수 있습니다(지금 내용은 새 내용으로 바뀝니다)."


# ---------- 넣기 ----------

class Sink:
    """한 번의 적재 동안 쓰는 연결과 넣을 곳. put으로 묶음을 넣고, 다 넣으면 done, 끝나면 늘 close."""

    def __init__(self, config, password, arrow_schema, resume):
        self.table = table = config["load"]["table"]
        self.schema, self.keys, self.last = table["schema"], table["key"], ""
        self.link = link = table["connection"]
        self.con = connect(link, password)
        try:
            file_columns = arrow_columns(arrow_schema)
            real, columns = look(self.con, self.schema, table["name"])
            plan(table, real, columns, file_columns, resume)
            self.name, self.staging, self.fresh = real or table["name"], None, False  # fresh: 넣을 곳을 새로 만들었음(이어 넣을 것이 없음)
            if real is None:  # 새 테이블(바꾸기여도 없던 테이블이면 바로 만든다)
                create(self.con, self.schema, self.name, arrow_schema)
                remember(link, self.schema, self.name, file_columns)
                self.fresh = True
            elif table["how"] == "replace":  # 새 내용은 옆 테이블에 넣었다가 다 넣은 뒤 바꿔치기
                self.staging = real + STAGING
                there, _ = look(self.con, self.schema, self.staging)
                if there is not None and not (resume and mine(link, self.schema, there)):
                    if mine(link, self.schema, there) is None:
                        raise ValueError(f"바꾸기에 쓸 임시 테이블 이름 '{there}'이 이미 있습니다(이 프로그램이 만든 것이 아님).")
                    drop(self.con, self.schema, there)  # 지난번에 다 넣지 못하고 남은 임시 테이블
                    there = None
                if there is None:
                    create(self.con, self.schema, self.staging, arrow_schema)
                    remember(link, self.schema, self.staging, file_columns)
                    self.fresh = True
                self.columns = file_columns
        except Exception:
            source.close(self.con)
            raise

    def put(self, data):
        """묶음 하나를 넣는다. (넣은 수, 건너뛴 수, 실패 수). 넣지 못하면 오류(다시 시도는 부르는 쪽이)."""
        rows = data.num_rows
        if self.keys:  # 다시 시도할 때도 다시 본다(확정 직후 끊겨 들어간 묶음이 겹치지 않게).
            import pyarrow as pa
            have = existing(self.con, self.schema, self.name, self.keys, data)
            if have:
                keys = list(zip(*(data.column(k).to_pylist() for k in self.keys)))
                data = data.filter(pa.array([k not in have for k in keys]))
        if data.num_rows:
            insert(self.con, self.schema, self.staging or self.name, data)
        return data.num_rows, rows - data.num_rows, 0

    def done(self):
        """바꾸기면 다 넣은 새 내용으로 바꿔치기한다. 그 직전에 원래 테이블이 여전히 이 프로그램이 만든 그대로인지 다시 본다."""
        if not self.staging:
            return
        real, columns = look(self.con, self.schema, self.name)
        record = mine(self.link, self.schema, self.name) if real else None
        if real is None or record is None or differences(record["columns"], columns):
            raise ValueError("넣는 동안 테이블이 바뀌어 바꾸지 않았습니다. 새 내용은 임시 테이블 '" + self.staging + "'에 남아 있습니다.")
        swap(self.con, self.schema, self.name, self.staging)
        remember(self.link, self.schema, self.name, self.columns)
        remember(self.link, self.schema, self.staging, None)

    def close(self):
        source.close(self.con)
