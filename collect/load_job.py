"""적재(수집 3단계): 결과 파일(output.path)을 묶음으로 읽어(Parquet은 pyarrow, CSV는 DuckDB. 메모리에 다 올리지 않음) DB 테이블(load_table)이나 Kafka 토픽(load_kafka)에 넣는다.
결과 파일은 지우거나 고치지 않는다. 넣은 데까지는 결과 파일 옆 '이름.load.json'(대상 서명, 넣은 행·묶음 수)에 적어 같은 설정으로 다시 하면 이어 넣는다.
비밀번호는 인자로만 받아 작업 동안 메모리에 두고 어디에도 적지 않는다."""
import hashlib
import json
import math
from pathlib import Path
from urllib.parse import urlsplit

from collect import load_kafka
from collect import load_table
from core import settings
from core import source
from collect import uploads
from collect.collect_http import HIDE, STOPPED, Halt

TARGETS = {"table": load_table, "kafka": load_kafka}
BACKOFF = 1.0  # 실패한 묶음을 다시 넣기 전에 기다리는 시간(초). 시도마다 두 배, MAX_BACKOFF까지.
MAX_BACKOFF = 60
SINGLE_SECONDS = 0.05  # 한 건씩 넣을 때 한 건에 드는 시간의 어림(초). 예상 시간 안내에만 쓴다.


# ---------- 구성 ----------

def check(load):
    """구성의 load 칸 모양 검사(파일·연결 없이). collect_job.normalize가 부른다."""
    table = load["table"]
    for where, value in (("table.schema", table["schema"]), ("table.name", table["name"]), ("table.connection.file", table["connection"]["file"]),
                         ("table.connection.url", table["connection"]["url"]), ("table.connection.user", table["connection"]["user"]),
                         ("kafka.destination", load["kafka"]["destination"]), ("kafka.key", load["kafka"]["key"])):
        if not isinstance(value, str):
            raise ValueError(f"구성의 load.{where}는 글자여야 합니다.")
    if not isinstance(table["key"], list) or not all(isinstance(k, str) and k for k in table["key"]):
        raise ValueError("구성의 load.table.key는 컬럼 이름 목록이어야 합니다.")
    if not isinstance(table["load_id"], bool):
        raise ValueError("구성의 load.table.load_id는 true 또는 false여야 합니다.")
    link = urlsplit(table["connection"]["url"].strip())
    if link.password is not None or any(source.secret_param(piece) for piece in link.query.split("&")):  # user:pw@ 또는 ?password=·?api_key= 같은 query
        raise ValueError("DB 주소에 비밀번호를 넣지 마세요. 비밀번호는 저장하지 않고 시작할 때 따로 입력합니다.")


def ready(config):
    """시작 전 검사(연결 없이). 적재하지 않으면(target이 file) 아무것도 하지 않는다."""
    target = config["load"]["target"]
    if target != "file":
        TARGETS[target].ready(config)


def record_path(path):
    return path.with_name(path.stem + ".load.json")


def signature(config):
    """이어 넣기를 정하는 대상 서명: 적재할 곳(연결·스키마·이름·방식·키 또는 Kafka 서버·토픽·키), 한 건씩인지, 묶음 크기. 비밀번호는 넣지 않는다."""
    load = config["load"]
    key = {"target": load["target"], "to": TARGETS[load["target"]].sign(config), "unit": load["unit"], "batch": load["batch"]}
    return hashlib.sha256(json.dumps(key, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def read_record(path):
    try:
        found = json.loads(record_path(path).read_text(encoding="utf-8"))
        return found if isinstance(found, dict) and isinstance(found.get("rows"), int) else None
    except (OSError, ValueError):
        return None


def write_record(path, record):
    """진행 기록을 임시 파일에 쓴 뒤 바꿔치기한다. 쓰는 중에 꺼져 기록이 깨지면 처음부터 다시 넣어 행이 두 번 들어가기 때문이다."""
    settings.write_atomic(record_path(path), record)


# ---------- 결과 파일 읽기 ----------

def file_stamp(path):
    """결과 파일의 크기·수정 시각(ns). 적재 기록에 적어 두었다가 같은 파일일 때만 이어 넣는다."""
    found = Path(path).stat()
    return [found.st_size, found.st_mtime_ns]


def total_rows(path):
    """결과 파일의 행 수. Parquet은 파일 정보로 알고, CSV는 다 읽어 보기 전에는 모른다(None)."""
    if path.suffix.lower() != ".parquet" or not path.is_file():
        return None
    import pyarrow.parquet as pq
    with open(path, "rb") as f:
        return pq.ParquetFile(f).metadata.num_rows


def file_schema(path):
    """결과 파일의 pyarrow 스키마(앞부분만 읽는다)."""
    with Batches(path, 1) as batches:
        return batches.schema


class Batches:
    """결과 파일을 size행씩 읽는다(마지막 묶음만 작을 수 있음). 덩어리로 읽어 메모리에 다 올리지 않는다. with로 열고 닫는다."""

    def __init__(self, path, size):
        import duckdb
        import pyarrow.parquet as pq
        parquet = Path(path).suffix.lower() == ".parquet"
        self.size, self.file = size, open(path, "rb") if parquet else duckdb.connect()  # file: 끝날 때 닫을 것
        try:
            if parquet:
                reader = pq.ParquetFile(self.file)
                self.schema, self.parts = reader.schema_arrow, reader.iter_batches(batch_size=size)
            else:  # 수집 결과 확인(collect_job.finish_file)처럼 DuckDB가 구분자를 알아내고, 타입은 파일 전체를 보고 정한다(앞부분만 보면 00001이 정수가 되고 앞이 빈 컬럼이 null 타입이 된다)
                self.parts = self.file.read_csv(str(path), sample_size=-1).to_arrow_reader(size)
                self.schema = self.parts.schema
        except Exception:
            self.file.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *error):
        self.file.close()

    def __iter__(self):
        import pyarrow as pa
        held, count = [], 0
        for part in self.parts:
            while part.num_rows:
                take = min(self.size - count, part.num_rows)
                held.append(part.slice(0, take))
                count, part = count + take, part.slice(take)
                if count == self.size:
                    yield pa.Table.from_batches(held, self.schema)
                    held, count = [], 0
        if count:
            yield pa.Table.from_batches(held, self.schema)


# ---------- 넣기 ----------

def attempt(work, retries, halt, note):
    """work()를 하고, 실패하면 retries번까지 간격을 늘리며 다시 한다. 멈추면 기다리다가도 바로 그만둔다."""
    for n in range(retries + 1):
        try:
            found = work()
            if n:
                note(wait="")
            return found
        except Exception as e:
            if n == retries:
                raise
            note(wait=f"넣지 못해 다시 시도합니다({n + 1}/{retries}): {str(e)[:300]}")
            if halt.wait(min(MAX_BACKOFF, BACKOFF * 2 ** n)):
                raise Halt(STOPPED, failed=False) from None


def run(config, path, password, halt, note):
    """결과 파일을 적재한다. note(**바뀐 상태)로 진행을 알린다(load 칸과 message). 실패하면 넣은 데까지 기록을 남기고 오류를 낸다.
    적재 한 번마다 업로드 기록(uploads.Run)에 넣은 행·바이트·범위를 남긴다. 테이블에 적재 ID를 넣기로 했으면 이어 넣어도 같은 ID를 쓴다."""
    load = config["load"]
    module, size = TARGETS[load["target"]], load["batch"]
    sign = signature(config)
    stamp = file_stamp(path)
    new_record = lambda: {"sign": sign, "file": stamp, "rows": 0, "batches": 0, "inserted": 0, "skipped": 0, "failures": 0}  # 처음부터 넣는 기록
    record = read_record(path)
    resumed = bool(record and record.get("sign") == sign and record.get("file", stamp) == stamp)  # 결과 파일이 그사이 바뀌었으면 처음부터
    if not resumed:
        record = new_record()
    if load["target"] == "table" and load["table"]["load_id"]:
        record.setdefault("load_id", uploads.new_id())
    load_id = record.get("load_id")
    view = {"target": load["target"], "rows": 0, "total": total_rows(path), "batches": 0, "skipped": 0, "failures": 0, "where": module.where(config)}

    def show():
        view.update(rows=record["rows"], batches=record["batches"], skipped=record["skipped"], failures=record["failures"])
        note(load=dict(view))

    show()
    ledger = None
    try:
        with Batches(path, size) as batches:
            sink = module.Sink(config, password, batches.schema, resumed, load_id)
            try:
                if sink.fresh and resumed:  # 이어 넣을 곳이 없어져 새로 만들었으면 처음부터
                    record, resumed = {**new_record(), **({"load_id": load_id} if load_id else {})}, False
                target = {**module.sign(config), **({"name": load["table"]["name"]} if load["target"] == "table" else {"destination": load["kafka"]["destination"]})}
                ledger = uploads.Run(load["target"], path, target, load_id, resumed)
                write_record(path, record)
                if resumed:
                    note(message="이어 넣습니다.")
                show()
                start, first = divmod(record["rows"], size)
                for n, part in enumerate(batches):
                    if n < start:
                        continue
                    at = first if n == start else 0
                    units = [part.slice(at)] if load["unit"] == "batch" else [part.slice(i, 1) for i in range(at, part.num_rows)]
                    handled = failed = 0
                    for unit in units:
                        if halt.is_set():
                            raise Halt(STOPPED, failed=False)
                        try:
                            put, skipped, bad = attempt(lambda: sink.put(unit), load["retries"], halt, note)
                        except Exception:  # 끝내 못 넣거나 멈췄다: 이 묶음에서 이미 전달된 만큼은 기록에 남겨 이어 넣기가 겹치지 않게 한다
                            done, sent, bad = getattr(sink, "partial", lambda: (0, 0, 0))()
                            if done or sent or bad:
                                ledger.add(sent, **sink.placed)
                                record.update(rows=record["rows"] + done, inserted=record["inserted"] + sent, failures=record["failures"] + bad)
                                write_record(path, record)
                            raise
                        ledger.add(put, **sink.placed)
                        handled, failed = handled + unit.num_rows, failed + bad
                        record.update(rows=record["rows"] + unit.num_rows, inserted=record["inserted"] + put, skipped=record["skipped"] + skipped,
                                      failures=record["failures"] + bad)
                        write_record(path, record)
                        show()
                        if load["interval"] and halt.wait(load["interval"]):
                            raise Halt(STOPPED, failed=False)
                    record["batches"] = n + 1
                    write_record(path, record)
                    ledger.save()
                    show()
                    if handled and failed == handled:
                        raise Halt(f"{n + 1}번째 묶음을 하나도 넣지 못해 멈췄습니다. {sink.last}".strip())
                sink.done()
            finally:
                sink.close()
    except Halt as e:
        if ledger:
            ledger.end("failed" if e.failed else "stopped", source.mask(str(e), [password], HIDE) if e.failed else "")
        raise
    except Exception as e:
        if ledger:
            ledger.end("failed", source.mask(str(e), [password], HIDE) or type(e).__name__)
        raise
    ledger.end("done")
    view["total"] = record["rows"]
    show()
    record_path(path).unlink(missing_ok=True)  # 다 넣었다: 다음에 같은 설정으로 하면 처음부터 넣는다.
    return record


def note_for(config, path):
    """한 건씩 넣을 때 시작 전에 보여 줄 안내(estimate의 load_note). 결과 파일이 있으면 행 수로 예상 시간을 더한다."""
    load = config["load"]
    if load["target"] == "file" or load["unit"] != "single":
        return None
    rows = total_rows(path) if path is not None else None
    if not rows:
        return "한 건씩 넣으면 오래 걸릴 수 있습니다."
    minutes = math.ceil(rows * (SINGLE_SECONDS + load["interval"]) / 60)
    return f"한 건씩 넣으면 오래 걸릴 수 있습니다(예상 약 {minutes:,}분)."
