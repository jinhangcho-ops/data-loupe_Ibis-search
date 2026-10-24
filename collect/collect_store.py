"""수집 작업의 상태(status·current·update)와 받은 것 저장(쪽 파일·진행 기록), 결과 파일 만들기(finish_api).
API 방식(collect_job)과 웹페이지 방식(collect_web)이 같이 쓴다. 지우는 것은 collect_job의 이름 규칙(.part)의 것뿐이다."""
import codecs
import datetime as dt
import email.utils
import json
import os
import re
import threading
import time
from pathlib import Path

from lazy import ibis

from collect import collect_http
from collect import collect_limits
from collect import collect_request as req
from core import scratch
from core import settings
from core import source
from collect.collect_http import Balancer, Halt

SAVE_EVERY = 0.5  # 진행 기록을 적는 간격(초). 쪽·조각마다 쓰면 기록이 커질수록 느려진다(쪽 수의 제곱)
SKEW = 300  # 응답의 Date와 PC 시계가 이보다 많이 다르면(초) 알린다.
ORDER = "__ibis_n"  # JSON 줄에 같이 적는 받은 순서(결과에서는 뺀다)
PAGE_FILE = re.compile(r"\d{7}\.jsonl(\.tmp)?")  # .part/ 폴더 안에서 이 작업이 만드는 파일
IDLE = {"running": False, "phase": "대기", "mode": "", "pages": 0, "requests": 0, "rows": 0, "bytes": 0, "total_bytes": None, "failures": 0,
        "concurrency": 0, "wait": "", "message": "", "error": "", "output": "", "started": None, "elapsed": 0.0, "group": "", "load": None}  # 처음 상태
status = dict(IDLE)
guard = threading.Lock()  # status와 진행 기록을 고칠 때
current = {"thread": None, "halt": threading.Event(), "balancer": None, "hide": []}


def write_record(path, record):
    """진행 기록을 임시 파일에 쓴 뒤 바꿔치기한다(쓰는 중에 꺼져도 기록이 반쯤 깨지지 않게)."""
    settings.write_atomic(path, record)


def keep_record(found, record, now=False):
    """진행 기록을 쓴다. now가 아니면 SAVE_EVERY초에 한 번만. guard 안에서 부른다(쓰는 기록이 한 시점의 모습이게).
    갑자기 끝나면 마지막으로 쓴 뒤에 받은 쪽은 이어 받을 때 다시 받는다(쪽 파일은 번호로 덮어써 겹치지 않는다)."""
    at = time.monotonic()
    if now or at - found.get("saved", -SAVE_EVERY) >= SAVE_EVERY:
        write_record(found["record"], record)
        found["saved"] = at


def clean(found):
    """이 작업이 만든 것만 지운다: .part 폴더 안의 쪽 파일과 빈 폴더(또는 .part 파일), 진행 기록, 바꾸는 중의 결과."""
    part = found["part"]
    if part.is_dir() and not part.is_symlink() and not os.path.isjunction(part):
        for item in part.iterdir():
            if PAGE_FILE.fullmatch(item.name) and item.is_file():
                item.unlink()
        try:
            part.rmdir()
        except OSError:  # 모르는 것이 남아 있으면 폴더는 둔다.
            pass
    elif part.is_file():
        part.unlink()
    for name in ("record", "out"):
        found[name].unlink(missing_ok=True)


def update(**changes):
    with guard:
        for key in ("message", "error", "wait"):
            if key in changes:
                changes[key] = mask(changes[key])
        status.update(changes)


def mask(text):
    return req_mask(text, current["hide"])


def req_mask(text, hide):
    """env 값을 ●●●로 가린다."""
    return source.mask(str(text), hide, collect_http.HIDE)


def balancer_for(config, halt, concurrency):
    """설정의 balance와 사용 한도 묶음으로 Balancer를 만든다. 동시 수·간격은 둘 중 엄한 쪽."""
    name = config["limit_group"]
    group = collect_limits.groups().get(name) if name else None
    if group:
        concurrency = min(concurrency, group["concurrency"])
    interval = max(config["balance"]["interval"], group["min_interval"] if group else 0)
    clock = {"checked": False}

    def heard(reply):
        if not clock["checked"] and reply.header("Date"):  # 서버 시계가 PC와 많이 다르면 하루 기간이 어긋날 수 있다.
            clock["checked"] = True
            try:
                gap = email.utils.parsedate_to_datetime(reply.header("Date")).timestamp() - time.time()
                if abs(gap) > SKEW:
                    update(message=f"서버 시계와 PC 시계가 {round(abs(gap) / 60)}분 다릅니다. PC 시계를 확인하세요(하루 한도의 기준 시각이 어긋날 수 있습니다).")
            except (TypeError, ValueError):
                pass
        return collect_limits.server_says(name, reply.headers)

    balancer = Balancer(concurrency, interval, config["balance"]["retries"], halt, note=lambda text: update(wait=text, phase="쉬는 중" if text else "받는 중"),
                        take=(lambda: collect_limits.take(name, halt)) if group else (lambda: None), heard=heard, secrets=current["hide"])
    current["balancer"] = balancer
    return balancer


def began(record):
    """이 수집이 시작된 시각(진행 기록에 처음 한 번 저장). {today}·{now}는 쪽마다 새로 재지 않고 이 시각으로 고정한다(이어 받아도 같다)."""
    return dt.datetime.fromisoformat(record.setdefault("began", dt.datetime.now().isoformat(timespec="seconds")))


def save_page(found, record, i, rows, size, **more):
    """쪽 하나(행 {컬럼: 값}들)를 .part 폴더에 쓰고 진행 기록에 더한다. 처음 보는 컬럼은 기록하고 값 종류를 모은다."""
    lines = []
    with guard:
        for n, row in enumerate(rows):
            out = {}
            for name, value in row.items():
                name = record["rename"].get(name, name)
                if name not in record["kinds"]:
                    taken = {c.lower() for c in record["columns"]}
                    if name.lower() in taken or name == ORDER:  # DuckDB 컬럼 이름은 대소문자를 가리지 않는다.
                        k = 2
                        while f"{name}_{k}".lower() in taken:
                            k += 1
                        record["rename"][name], name = f"{name}_{k}", f"{name}_{k}"
                    record["columns"].append(name)
                    record["kinds"][name] = []
                kind = req.kind_of(value)
                if kind and kind not in record["kinds"][name]:
                    record["kinds"][name].append(kind)
                out[name] = value
            out[ORDER] = i * 10**7 + n
            lines.append(json.dumps(out, ensure_ascii=False))
    if lines:
        file = found["part"] / f"{i:07d}.jsonl"
        temp = file.with_name(file.name + ".tmp")
        temp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.replace(temp, file)
    with guard:
        record["done"].append(i)
        record["rows"] += len(rows)
        record["bytes"] += size
        record.update(more)
        keep_record(found, record)
        status.update(pages=len(record["done"]), rows=record["rows"], bytes=record["bytes"])


def duck():
    """결과를 만들 DuckDB(메모리). 설정의 작업 메모리 상한과 넘칠 때 쓸 임시 폴더를 준다. (연결, 임시 폴더)"""
    folder = scratch.make()
    memory = settings.load().get("memory")
    try:
        con = ibis.duckdb.connect(temp_directory=folder, **({"memory_limit": f"{memory}MB"} if memory else {}))
    except Exception:
        scratch.remove(folder)
        raise
    return con, folder


def finish_api(config, path, found, record):
    """받은 쪽 파일들을 하나의 결과 파일로 바꾼다(타입은 전체를 보고 정함, 중복 제거). 결과 파일을 새로 만들었으면 True, 받은 기록이 없어 만들지 않았으면 False."""
    update(phase="바꾸는 중", wait="")
    end = record.get("end")
    files = [found["part"] / f"{i:07d}.jsonl" for i in sorted(record["done"]) if end is None or i < end]
    files = [str(f) for f in files if f.exists()]
    if not files:
        clean(found)
        update(message="받은 기록이 없어 결과 파일을 만들지 않았습니다.")
        return False
    types = {name: req.column_type(record["kinds"].get(name, [])) for name in record["columns"]}
    con, folder = duck()
    try:
        table = con.read_json(files, columns={**types, ORDER: "BIGINT"}, format="newline_delimited")
        table = deduped(table, config["output"]["dedupe"], record["rename"])
        rows = write_output(con, table.order_by(ORDER).drop(ORDER), config["output"]["format"], found["out"], folder)
    finally:
        con.disconnect()
        scratch.remove(folder)
    replace(found, path)
    update(rows=rows)
    return True


def deduped(table, columns, rename=None):
    """columns가 같은 행 중 받은 순서로 첫 행만 남긴다."""
    if not columns:
        return table
    columns = [(rename or {}).get(c, c) for c in columns]
    missing = [c for c in columns if c not in table.columns]
    if missing:
        raise Halt(f"중복 기준 컬럼이 결과에 없습니다: {', '.join(missing)}")
    first = ibis.row_number().over(group_by=columns, order_by=table[ORDER])
    return table.mutate(_first=first).filter(lambda t: t._first == 0).drop("_first")


def write_output(con, table, fmt, out, folder):
    """table을 out(parquet 또는 csv)으로 쓰고 행 수를 돌려준다. CSV는 Excel이 한글을 바로 읽도록 UTF-8 BOM을 붙인다."""
    if fmt == "parquet":
        con.to_parquet(table, out)
        import pyarrow.parquet as pq  # 쓴 파일의 정보에서 행 수를 읽는다(다시 읽어 세지 않는다)
        return pq.read_metadata(out).num_rows
    plain = Path(folder, "result.csv")
    con.to_csv(table, plain)
    with_bom(plain, out)
    return con.execute(table.count())


def with_bom(given, out, decode=None):
    """given 파일을 맨 앞에 UTF-8 BOM을 붙여 out에 쓴다. decode가 있으면(예: cp949) 그 인코딩을 UTF-8로 바꾼다. 원래 있던 BOM은 하나로."""
    decoder = codecs.getincrementaldecoder(decode)("replace") if decode else None
    with open(given, "rb") as f, open(out, "wb") as w:
        w.write(codecs.BOM_UTF8)
        first = True
        for chunk in iter(lambda: f.read(1 << 20), b""):
            if decoder:
                chunk = decoder.decode(chunk).encode("utf-8")
            if first and chunk.startswith(codecs.BOM_UTF8):
                chunk = chunk[len(codecs.BOM_UTF8):]
            first = False
            w.write(chunk)
        if decoder:
            w.write(decoder.decode(b"", final=True).encode("utf-8"))


def replace(found, path):
    """다 만든 결과(.part.형식)로 결과 파일을 바꿔치기하고 작업 파일을 정리한다."""
    try:
        os.replace(found["out"], path)
    except PermissionError:
        found["out"].unlink(missing_ok=True)
        raise Halt("다른 프로그램이 파일을 쓰고 있습니다. 그 프로그램에서 결과 파일을 닫고 다시 시작하세요(받은 것은 남아 있어 이어서 바꿉니다).") from None
    clean(found)
