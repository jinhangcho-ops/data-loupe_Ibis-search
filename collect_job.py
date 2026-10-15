"""수집 작업: 설정(config) 검사, 한 번에 하나씩 뒤에서 도는 작업(API 쪽 넘기기, 파일 조각 받기), 받은 것을 결과 파일로 바꾸기, 상태.
받는 것은 바로 디스크에 둔다: 결과 경로 옆 '이름.part/'(API, 쪽마다 JSON 줄 파일)나 '이름.part'(파일 모드), 진행 기록 '이름.part.json'.
같은 설정으로 다시 시작하면 이어 받는다. 지우는 것은 이 이름 규칙의 것뿐이다."""
import codecs
import datetime as dt
import email.utils
import hashlib
import json
import math
import os
import re
import shutil
import threading
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from lazy import ibis

import collect_http
import collect_limits
import collect_request as req
import collect_web
import load_job
import scratch
import server
import settings
import source
from collect_http import Balancer, Halt, Reply

VERSION = 1
MIN_FREE = 200 * 1024 * 1024  # 시작할 때 결과 폴더의 디스크에 적어도 남아 있어야 하는 공간
SKEW = 300  # 응답의 Date와 PC 시계가 이보다 많이 다르면(초) 알린다.
SAVE_EVERY = 0.5  # 파일 조각의 진행 기록을 적는 간격(초)
ORDER = "__ibis_n"  # JSON 줄에 같이 적는 받은 순서(결과에서는 뺀다)
PAGE_FILE = re.compile(r"\d{7}\.jsonl(\.tmp)?")  # .part/ 폴더 안에서 이 작업이 만드는 파일
DEFAULT = {
    "version": VERSION, "mode": "api", "env_path": "", "limit_group": "",
    "request": {"method": "GET", "url": "", "query": [], "headers": [], "body_kind": "none", "body": ""},
    "response": {"records": "", "columns": []},
    "paging": {"kind": "none", "start": 1, "step": 100, "max_pages": 100, "cursor_path": "", "next_path": "", "total_path": ""},
    "balance": {"concurrency": 2, "interval": 1.0, "retries": 5, "timeout": 30},
    "file": {"url": "", "headers": [], "chunks": 4, "kind": "auto", "encoding": "auto"},
    "output": {"format": "parquet", "path": "", "dedupe": []},
    # 웹페이지: 주소({page}·{today}·{now}), 항목 선택자, 컬럼 [{name, selector(항목 안 상대, ""는 항목 자체), attr("text" 또는 속성 이름)}],
    # 쪽 넘기기(none·next·url·scroll), 항목을 기다리는 최대 초, robots.txt 지키기, 실행 중 창 보이기
    "web": {"url": "", "items": "", "fields": [], "paging": {"kind": "none", "next": "", "start": 1, "max_pages": 100}, "wait": 10, "robots": True, "show": True},
    "plain": [],  # 비밀 같은 이름이지만 사용자가 비밀이 아니라고 한 이름들(collect_request.literal_places)
    # 적재: 결과 파일을 그대로 둘지(file), DB 테이블이나 Kafka 토픽에 넣을지. 비밀번호 칸은 없다(시작할 때 따로 보내고 저장하지 않는다).
    # 연결은 kind가 folder면 file(내 폴더의 DuckDB 파일), url이면 url·user. how: new·append·replace, key는 append에서 이미 있는 키를 건너뛸 컬럼들.
    "load": {"target": "file", "table": {"connection": {"kind": "folder", "file": "", "url": "", "user": ""}, "schema": "", "name": "", "how": "new", "key": []},
             "kafka": {"destination": "", "key": ""}, "unit": "batch", "batch": 10000, "interval": 0.0, "retries": 3},
}
CHOICES = {("mode",): ("api", "file", "web"), ("web", "paging", "kind"): ("none", "next", "url", "scroll"), ("request", "body_kind"): req.BODY_KINDS, ("paging", "kind"): ("none", "page", "offset", "cursor", "next_url"),
           ("file", "kind"): ("auto", "csv", "parquet", "json"), ("file", "encoding"): ("auto", "utf-8", "cp949"), ("output", "format"): ("parquet", "csv"),
           ("load", "target"): ("file", "table", "kafka"), ("load", "table", "connection", "kind"): ("folder", "url"), ("load", "table", "how"): ("new", "append", "replace"),
           ("load", "unit"): ("batch", "single")}
NUMBERS = {("paging", "start"): (int, -10**12, 10**12), ("paging", "step"): (int, 1, 10**9), ("paging", "max_pages"): (int, 1, 1_000_000),
           ("balance", "concurrency"): (int, 1, 32), ("balance", "interval"): (float, 0, 3600), ("balance", "retries"): (int, 0, 20),
           ("balance", "timeout"): (float, 1, 600), ("file", "chunks"): (int, 1, 16),
           ("web", "paging", "start"): (int, -10**12, 10**12), ("web", "paging", "max_pages"): (int, 1, 1_000_000), ("web", "wait"): (float, 1, 120),
           ("load", "batch"): (int, 100, 1_000_000), ("load", "interval"): (float, 0, 3600), ("load", "retries"): (int, 0, 20)}
MAX_FIELDS = 200  # 웹페이지 컬럼 수
FIELD_ATTR = re.compile(r"text|[A-Za-z_:][-A-Za-z0-9_:.]{0,99}")  # 컬럼의 attr: text 또는 속성 이름
RUNNING = "이미 수집이 돌고 있습니다. 끝나거나 멈춘 뒤에 시작하세요."

IDLE = {"running": False, "phase": "대기", "mode": "", "pages": 0, "requests": 0, "rows": 0, "bytes": 0, "total_bytes": None, "failures": 0,
        "concurrency": 0, "wait": "", "message": "", "error": "", "output": "", "started": None, "elapsed": 0.0, "group": "", "load": None}  # 처음 상태
status = dict(IDLE)
guard = threading.Lock()  # status와 진행 기록을 고칠 때
current = {"thread": None, "halt": threading.Event(), "balancer": None, "hide": []}


# ---------- 설정 ----------

def normalize(config):
    """설정을 검사하고 빠진 칸을 기본값으로 채운 새 설정. 모르는 칸이 있으면 거부한다."""
    if not isinstance(config, dict):
        raise ValueError("구성이 올바르지 않습니다.")
    if config.get("version", VERSION) != VERSION:
        raise ValueError(f"구성의 version은 {VERSION}이어야 합니다.")
    unknown = []
    found = merged(DEFAULT, config, "", unknown)
    if unknown:
        if any(req.secret_name(name.rsplit(".", 1)[-1]) for name in unknown):
            raise ValueError("비밀번호 같은 비밀 값은 구성에 넣지 않습니다(저장하지 않고 시작할 때 따로 입력합니다).")
        raise ValueError(f"구성에 쓸 수 없는 값이 있습니다: {', '.join(unknown)}")
    texts = [("env_path",), ("limit_group",), ("request", "method"), ("request", "url"), ("request", "body"), ("response", "records"),
             ("paging", "cursor_path"), ("paging", "next_path"), ("paging", "total_path"), ("file", "url"), ("output", "path"),
             ("web", "url"), ("web", "items"), ("web", "paging", "next")]
    for where in texts:
        if not isinstance(at(found, where), str):
            raise ValueError(f"구성의 {'.'.join(where)}는 글자여야 합니다.")
    for where, options in CHOICES.items():
        if at(found, where) not in options:
            raise ValueError(f"구성의 {'.'.join(where)}는 {', '.join(options)} 중 하나입니다.")
    for where, (kind, low, high) in NUMBERS.items():
        value = at(found, where)
        if type(value) not in ((int,) if kind is int else (int, float)) or not low <= value <= high:
            raise ValueError(f"구성의 {'.'.join(where)}는 {low}~{high}의 {'정수' if kind is int else '수'}여야 합니다.")
    if not re.fullmatch(r"[A-Za-z]+", found["request"]["method"]):
        raise ValueError("요청 방식(method)은 영문자로 쓰세요(예: GET, POST).")
    found["request"]["method"] = found["request"]["method"].upper()
    for where in (("request", "query"), ("request", "headers"), ("file", "headers")):
        value = at(found, where)
        if not isinstance(value, list) or not all(isinstance(p, list) and len(p) == 2 and all(isinstance(x, str) for x in p) for p in value):
            raise ValueError(f"구성의 {'.'.join(where)}는 [[이름, 값], …] 모양이어야 합니다.")
    columns = found["response"]["columns"]
    if not isinstance(columns, list) or not all(isinstance(c, dict) and set(c) == {"name", "path"} and isinstance(c["name"], str) and c["name"].strip()
                                                and isinstance(c["path"], str) for c in columns):
        raise ValueError('구성의 response.columns는 [{"name", "path"}, …] 모양이어야 합니다(이름은 비우지 않음).')
    if len({c["name"].lower() for c in columns}) != len(columns):
        raise ValueError("컬럼 이름이 겹칩니다(대소문자 무시).")
    if not isinstance(found["plain"], list) or not all(isinstance(n, str) and n for n in found["plain"]):
        raise ValueError("구성의 plain은 이름 목록이어야 합니다.")
    dedupe = found["output"]["dedupe"]
    if not isinstance(dedupe, list) or not all(isinstance(c, str) and c for c in dedupe):
        raise ValueError("구성의 output.dedupe는 컬럼 이름 목록이어야 합니다.")
    check_web(found["web"])
    load_job.check(found["load"])
    return found


def merged(default, given, where, unknown):
    """given에 빠진 칸을 default로 채운 새 값(묶인 칸도 안까지). 모르는 칸의 이름은 unknown에 더한다."""
    if not isinstance(given, dict):
        raise ValueError(f"구성의 {where}가 올바르지 않습니다." if where else "구성이 올바르지 않습니다.")
    unknown += [f"{where}.{k}" if where else k for k in given if k not in default]
    return {key: merged(value, given.get(key, value), f"{where}.{key}" if where else key, unknown) if isinstance(value, dict) else given.get(key, value)
            for key, value in default.items()}


def check_web(web):
    """웹페이지 칸 검사: 참거짓, 주소·선택자 길이, 컬럼 모양."""
    for key in ("robots", "show"):
        if type(web[key]) is not bool:
            raise ValueError(f"구성의 web.{key}는 true·false여야 합니다.")
    if max(len(web["url"]), len(web["items"]), len(web["paging"]["next"])) > 2000:
        raise ValueError("웹페이지 주소·선택자는 2,000자까지입니다.")
    fields = web["fields"]
    if not isinstance(fields, list) or len(fields) > MAX_FIELDS or not all(
            isinstance(f, dict) and set(f) == {"name", "selector", "attr"} and all(isinstance(f[k], str) for k in f) and f["name"].strip()
            and len(f["name"]) <= 200 and len(f["selector"]) <= 2000 and FIELD_ATTR.fullmatch(f["attr"]) for f in fields):
        raise ValueError(f'구성의 web.fields는 [{{"name", "selector", "attr"}}, …] 모양이어야 합니다(이름은 비우지 않음, attr은 text 또는 속성 이름, {MAX_FIELDS}개까지).')
    if len({f["name"].lower() for f in fields}) != len(fields):
        raise ValueError("컬럼 이름이 겹칩니다(대소문자 무시).")


def at(config, where):
    value = config
    for key in where:
        value = value[key]
    return value


def ready(config, output=True):
    """시험·시작 전 검사. (설정, env 값들). 비밀처럼 보이는 자리에 남은 글자 그대로의 값과 env 파일에 없는 이름은 거부한다."""
    config = normalize(config)
    target = {"api": config["request"]["url"], "file": config["file"]["url"], "web": config["web"]["url"]}[config["mode"]]
    if not target.strip():
        raise ValueError("주소를 입력하세요.")
    if config["mode"] == "web":
        collect_web.ready(config["web"])
    check_literals(config)
    if output and not config["output"]["path"].strip():
        raise ValueError("결과 파일 경로를 고르세요.")
    names = req.env_names(config)
    env = req.read_env(config["env_path"]) if config["env_path"].strip() else {}
    missing = [n for n in names if n not in env]
    if missing:
        raise ValueError(f"env 파일{'을 고르세요. 쓰인 이름' if not config['env_path'].strip() else '에 없는 이름이 있습니다'}: {', '.join(missing)}")
    if config["limit_group"] and config["limit_group"] not in collect_limits.groups():
        raise ValueError(f"사용 한도 묶음 '{config['limit_group']}'이 없습니다.")
    return config, env


def check_literals(config):
    places = req.literal_places(config)
    if places:
        raise ValueError(f"비밀처럼 보이는 자리에 값이 그대로 들어 있습니다: {', '.join(places)}. env 파일에 넣고 {{env:이름}}으로 바꾸세요.")


def output_path(config):
    """결과 파일 경로. 형식의 확장자로 끝나지 않으면 붙인다."""
    path, fmt = config["output"]["path"].strip(), config["output"]["format"]
    return Path(path if path.lower().endswith("." + fmt) else f"{path}.{fmt}")


def parts(path):
    """결과 경로 옆의 작업 파일들: 받는 곳(API는 폴더, 파일 모드는 파일), 진행 기록, 바꾸는 중의 결과."""
    stem = path.with_name(path.stem + ".part")
    return {"part": stem, "record": path.with_name(path.stem + ".part.json"), "out": path.with_name(f"{path.stem}.part{path.suffix}")}


def digest(config):
    """이어 받기를 정하는 설정의 해시. 동시 수·간격·최대 쪽 수·결과 형식처럼 받은 내용이 바뀌지 않는 칸은 넣지 않는다."""
    if config["mode"] == "api":
        paging = {k: v for k, v in config["paging"].items() if k != "max_pages"}
        key = {"mode": "api", "request": config["request"], "response": config["response"], "paging": paging}
    elif config["mode"] == "file":
        key = {"mode": "file", "url": config["file"]["url"], "headers": config["file"]["headers"]}
    else:  # 웹페이지: 최대 쪽 수·기다리기·robots·보이기는 받은 내용을 바꾸지 않는다.
        web = config["web"]
        key = {"mode": "web", "url": web["url"], "items": web["items"], "fields": web["fields"], "paging": {k: v for k, v in web["paging"].items() if k != "max_pages"}}
    return hashlib.sha256(json.dumps(key, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def read_record(path):
    try:
        found = json.loads(path.read_text(encoding="utf-8"))
        return found if isinstance(found, dict) and "hash" in found else None
    except (OSError, ValueError):
        return None


def write_record(path, record):
    path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")


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


def resumable(config):
    """같은 설정으로 이어 받을 수 있는 진행 기록. 없으면 None."""
    if not config["output"]["path"].strip():
        return None
    record = read_record(parts(output_path(config))["record"])
    return record if record and record.get("hash") == digest(config) else None


# ---------- 상태 ----------

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


def snapshot():
    """상태(약속의 모양)."""
    with guard:
        found = {k: v for k, v in status.items() if k != "group"}
        balancer = current["balancer"]
        if balancer is not None and found["running"]:
            found.update(requests=balancer.sent, failures=balancer.failures, concurrency=balancer.limit)
        if found["started"] is not None and found["running"]:
            found["elapsed"] = round(time.time() - found["started"], 1)
        group = status["group"]
    found["usage"] = collect_limits.usage(group) if group else None
    return found


def running():
    thread = current["thread"]
    return thread is not None and thread.is_alive()


def stop(wait=0):
    """도는 작업을 멈추게 한다(받은 데까지 남긴다). wait초까지 끝나기를 기다린다."""
    current["halt"].set()
    thread = current["thread"]
    if wait and thread is not None:
        thread.join(wait)


# ---------- 시작 ----------

def start(config, password=""):
    """작업을 뒤에서 시작한다. (이어 받는지). password는 적재할 곳의 비밀번호로, 작업 동안 메모리에만 둔다."""
    if running():
        raise ValueError(RUNNING)
    config, env = ready(config)
    path = output_path(config)
    if not path.parent.is_dir():
        raise ValueError("결과 파일의 폴더가 없습니다.")
    if server.reading(path):
        raise ValueError("지금 읽고 있는 파일에는 저장할 수 없습니다. 다른 이름을 고르세요.")
    group = config["limit_group"]
    if group and collect_limits.load() is None:
        raise ValueError(collect_limits.broken_message())
    if shutil.disk_usage(path.parent).free < MIN_FREE:
        raise ValueError(f"디스크 공간이 모자랍니다(적어도 {MIN_FREE // 1024 // 1024}MB가 필요합니다).")
    load_job.ready(config)
    found = parts(path)
    record = read_record(found["record"])
    resumed = bool(record and record.get("hash") == digest(config))
    if not resumed:
        if record is not None or found["part"].exists() or found["out"].exists():
            clean(found)
        record = {"hash": digest(config), "mode": config["mode"], "done": [], "rows": 0, "bytes": 0}
        write_record(found["record"], record)
    launch(config, env, path, found, record, password, pages=len(record["done"]) if config["mode"] != "file" else 0, rows=record.get("rows", 0),
           bytes=record.get("bytes", 0), total_bytes=record.get("size"), message="이어 받습니다." if resumed else "", group=group)
    return resumed


def start_load(config, password=""):
    """이미 받은 결과 파일(output.path)을 다시 받지 않고 적재만 한다(적재가 실패한 뒤 다시 넣을 때)."""
    if running():
        raise ValueError(RUNNING)
    config = normalize(config)
    if config["load"]["target"] == "file":
        raise ValueError("적재할 곳(DB 테이블 또는 Kafka)을 고르세요.")
    if not config["output"]["path"].strip():
        raise ValueError("결과 파일 경로를 고르세요.")
    path = output_path(config)
    if not path.is_file():
        raise ValueError("적재할 결과 파일이 없습니다. 먼저 수집하세요.")
    load_job.ready(config)
    launch(config, {}, path, None, None, password, phase="적재 중", output=str(path))


def launch(config, env, path, found, record, password, **shown):
    """상태를 처음으로 돌리고(shown으로 고친 칸만 다르게) 작업 스레드를 띄운다."""
    halt = threading.Event()
    current.update(halt=halt, balancer=None, hide=req.hidden(env) + ([password] if password else []))
    update(**{**IDLE, "running": True, "phase": "받는 중", "mode": config["mode"], "started": time.time(), **shown})
    thread = threading.Thread(target=work, args=(config, env, path, found, record, halt, password), daemon=True)
    current["thread"] = thread
    thread.start()


def work(config, env, path, found, record, halt, password):
    """받기(record가 None이면 건너뜀) → 결과 파일이 있고 적재할 곳이 있으면 적재. 적재가 실패해도 결과 파일은 남는다."""
    try:
        if record is not None:
            {"api": run_api, "file": run_file, "web": collect_web.run_web}[config["mode"]](config, env, path, found, record, halt)
        if config["load"]["target"] != "file" and path.exists():
            update(phase="적재 중", output=str(path), wait="")
            load_job.run(config, path, password, halt, update)
        update(phase="끝", output=str(path) if path.exists() else "", wait="")
    except Halt as e:
        update(phase="실패" if e.failed else "멈춤", **({"error": str(e)} if e.failed else {"message": str(e)}), wait="")
    except Exception as e:
        update(phase="실패", error=str(e) or type(e).__name__, wait="")
    finally:
        with guard:
            balancer = current["balancer"]
            if balancer is not None:
                status.update(requests=balancer.sent, failures=balancer.failures, concurrency=balancer.limit)
            status.update(running=False, elapsed=round(time.time() - status["started"], 1))
        current["hide"] = []  # 비밀번호·env 값은 작업이 끝나면 버린다.


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


# ---------- API ----------

def first_page(config, env):
    """시험: 첫 쪽 요청을 한 번만 보낸다(다시 시도 없음, 사용 한도에는 센다). 비밀 값은 ●●●로 가린다."""
    hide = req.hidden(env)
    if config["mode"] == "api":
        sent = req.build(config["request"], context(config, env, 0))
    else:
        sent = req.build({"method": "GET", "url": config["file"]["url"], "headers": config["file"]["headers"] + [["Range", "bytes=0-65535"]]}, context(config, env, 0))
    if config["limit_group"]:
        collect_limits.take(config["limit_group"], threading.Event())
    reply = collect_http.fetch(sent, config["balance"]["timeout"], hide)
    collect_limits.server_says(config["limit_group"], reply.headers)
    text = req_mask(reply.text()[:200_000], hide)
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    shown = lambda value: req_mask(value, hide)
    return {"sent": {"method": sent["method"], "url": shown(sent["url"]), "headers": [[k, shown(v)] for k, v in sent["headers"]]},
            "status": reply.status, "headers": [[k, shown(v)] for k, v in reply.headers], "text": text, "json": data}


def context(config, env, i, cursor=""):
    paging = config["paging"]
    return {"env": env, "page": paging["start"] + i, "offset": paging["start"] + i * paging["step"], "cursor": cursor, "now": dt.datetime.now()}


def sample(config, text):
    """응답 예시로 기록·컬럼·다음 커서 등을 미리 본다."""
    config = normalize(config)
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        raise ValueError("응답 예시가 JSON이 아닙니다.") from None
    found = req.records(data, config["response"]["records"])
    if found is None:
        raise ValueError(f"기록 경로 '{config['response']['records']}'를 응답에서 찾지 못했습니다.")
    rows = req.rows(found, config["response"]["columns"])
    kinds = {}
    for row in rows:
        for name, value in row.items():
            kinds.setdefault(name, set())
            if req.kind_of(value):
                kinds[name].add(req.kind_of(value))
    paths = {c["name"]: c["path"] for c in config["response"]["columns"]}
    paging = config["paging"]
    return {"count": len(rows), "columns": [{"name": n, "path": paths.get(n, n), "type": req.TYPE_TEXT[req.column_type(k)]} for n, k in kinds.items()],
            "rows": rows[:20], "cursor": req.extract(data, paging["cursor_path"]), "next": req.extract(data, paging["next_path"]),
            "total": as_count(req.extract(data, paging["total_path"]))}


def as_count(value):
    """전체 수로 읽을 수 있는 값(정수·실수·숫자 글자)을 정수로. 아니면 None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    return int(value) if isinstance(value, str) and value.strip().isdigit() else None


def page_cap(config, record):
    """요청할 쪽 번호의 끝(이 번호는 요청하지 않는다): 최대 쪽 수, 빈 쪽, 전체 기록 수로 정한다."""
    paging, cap = config["paging"], config["paging"]["max_pages"]
    if record.get("end") is not None:
        cap = min(cap, record["end"])
    total, first = record.get("total"), record.get("first")
    if total is not None:
        per = paging["step"] if paging["kind"] == "offset" else first
        if per:
            cap = min(cap, math.ceil(total / per))
    return cap


def link_next(reply):
    """Link 헤더의 rel=next 주소."""
    for part in (reply.header("Link") or "").split(","):
        m = re.match(r'\s*<([^>]*)>\s*;(.*)', part)
        if m and re.search(r'rel\s*=\s*"?([^";]*\s)?next(\s[^";]*)?"?', m[2]):
            return m[1]
    return None


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
        write_record(found["record"], record)
        status.update(pages=len(record["done"]), rows=record["rows"], bytes=record["bytes"])


def run_api(config, env, path, found, record, halt):
    paging, response = config["paging"], config["response"]
    found["part"].mkdir(exist_ok=True)
    balancer = balancer_for(config, halt, config["balance"]["concurrency"] if paging["kind"] in ("page", "offset") else 1)
    timeout, hide = config["balance"]["timeout"], current["hide"]
    record.setdefault("columns", [])
    record.setdefault("kinds", {})
    record.setdefault("rename", {})

    def get(i, cursor="", url=None):
        sent = req.build(config["request"], context(config, env, i, cursor), url)
        reply = balancer.call(lambda: collect_http.fetch(sent, timeout, hide))
        try:
            data = json.loads(reply.text())
        except ValueError:
            raise Halt(f"{i + 1}번째 쪽의 응답이 JSON이 아닙니다.") from None
        rows, there = req.mapped(data, response)
        if not there and not record["done"]:
            raise Halt(f"기록 경로 '{response['records']}'를 응답에서 찾지 못했습니다.")
        return data, rows, reply

    def note_first(data, rows):
        if "first" not in record:
            record["first"] = len(rows)
            record["total"] = as_count(req.extract(data, paging["total_path"]))

    if paging["kind"] in ("none", "page", "offset"):
        if paging["kind"] == "none":
            config = {**config, "paging": {**paging, "max_pages": 1}}
        done, claimed, errors, quit = set(record["done"]), [0], [], threading.Event()

        def claim():
            with guard:
                while claimed[0] < page_cap(config, record) and not quit.is_set():
                    i, claimed[0] = claimed[0], claimed[0] + 1
                    if i not in done:
                        return i
                return None

        def worker():
            try:
                while (i := claim()) is not None:
                    _, rows, reply = get(i)
                    if not rows:  # 빈 쪽: 이 번호부터는 요청하지 않는다.
                        with guard:
                            record["end"] = i if record.get("end") is None else min(record["end"], i)
                    save_page(found, record, i, rows, len(reply.body))
            except Exception as e:
                errors.append(e)
                quit.set()
                halt.set()  # 다른 일꾼도 멈춘다.

        if 0 not in done:  # 첫 쪽을 먼저 받아 전체 수·쪽 크기를 안다.
            i = claim()
            data, rows, reply = get(i)
            with guard:
                note_first(data, rows)
                if not rows:
                    record["end"] = 0
            save_page(found, record, i, rows, len(reply.body))
        threads = [threading.Thread(target=worker, daemon=True) for _ in range(max(1, balancer.limit))]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        if errors:
            raise min(errors, key=lambda e: not getattr(e, "failed", True))  # 실패가 있으면 그것을, 아니면 멈춤
    elif not record.get("finished"):  # cursor·next_url: 하나씩, 받은 커서·주소를 따라간다.
        seen = set(record.get("seen", []))
        cursor, url = record.get("cursor", ""), record.get("next")
        i = len(record["done"])
        while i < paging["max_pages"]:
            if paging["kind"] == "next_url" and i > 0 and not url:
                break
            data, rows, reply = get(i, cursor, url if paging["kind"] == "next_url" and i > 0 else None)
            if i == 0:
                note_first(data, rows)
                if paging["kind"] == "next_url":
                    seen.add(reply.url)
            more = {}
            if paging["kind"] == "cursor":
                nxt = req.extract(data, paging["cursor_path"])
                nxt = None if nxt is None else str(nxt)
                end = not rows or nxt is None or nxt in seen
                more["cursor"] = nxt
            else:
                nxt = req.extract(data, paging["next_path"]) if paging["next_path"] else link_next(reply)
                nxt = None if nxt is None else urljoin(reply.url, str(nxt))
                end = not rows or nxt is None or nxt in seen
                more["next"] = nxt
            total = record.get("total")
            end = end or (total is not None and record["rows"] + len(rows) >= total)
            if nxt is not None:
                seen.add(nxt)
            save_page(found, record, i, rows, len(reply.body), seen=sorted(seen), finished=end, **more)
            if end:
                break
            cursor, url, i = more.get("cursor") or "", more.get("next"), i + 1
    finish_api(config, path, found, record)


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
    """받은 쪽 파일들을 하나의 결과 파일로 바꾼다(타입은 전체를 보고 정함, 중복 제거)."""
    update(phase="바꾸는 중", wait="")
    end = record.get("end")
    files = [found["part"] / f"{i:07d}.jsonl" for i in sorted(record["done"]) if end is None or i < end]
    files = [str(f) for f in files if f.exists()]
    if not files:
        clean(found)
        update(message="받은 기록이 없어 결과 파일을 만들지 않았습니다.")
        return
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
    else:
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


# ---------- 파일 ----------

def content_range(value):
    """Content-Range: bytes 0-0/1234 → (시작, 전체 크기). 전체 크기를 모르면 크기는 None."""
    m = re.match(r"bytes\s+(\d+)-\d+/(\d+|\*)", value or "")
    return (int(m[1]), None if m[2] == "*" else int(m[2])) if m else (None, None)


def merge(ranges):
    """[[시작, 끝)…]을 겹치거나 붙은 것끼리 합친다."""
    out = []
    for start, end in sorted(ranges):
        if out and start <= out[-1][1]:
            out[-1][1] = max(out[-1][1], end)
        else:
            out.append([start, end])
    return out


def gaps(done, size):
    """[0, size)에서 아직 받지 않은 구간들."""
    found, at_ = [], 0
    for start, end in merge(done):
        if start > at_:
            found.append([at_, start])
        at_ = max(at_, end)
    if at_ < size:
        found.append([at_, size])
    return found


def need_space(folder, size):
    free = shutil.disk_usage(folder).free
    if size is not None and free < size + MIN_FREE:
        raise Halt(f"디스크 공간이 모자랍니다(필요 약 {size // 1024 // 1024 + 1}MB, 남음 {free // 1024 // 1024}MB).")


def run_file(config, env, path, found, record, halt):
    spec, timeout, hide = config["file"], config["balance"]["timeout"], current["hide"]
    sent = req.build({"method": "GET", "url": spec["url"], "headers": spec["headers"]}, context(config, env, 0))
    balancer = balancer_for(config, halt, spec["chunks"])
    balancer.slow_on = (429, 503)
    raw = found["part"]
    info = {}
    converting = output_kind(config, sent["url"], None) != config["output"]["format"]

    def stream(reply, out, start, end):
        """응답 몸통을 out의 start부터 쓴다. end(끝, 없으면 끝까지)에 닿기 전에 끊기면 다시 시도하게 오류를 낸다."""
        position = start
        last_save = time.monotonic()
        with open(out, "r+b") as f:
            f.seek(start)
            while end is None or position < end:
                chunk = reply.read(1 << 20 if end is None else min(1 << 20, end - position))
                if not chunk:
                    if end is None:
                        break
                    raise ConnectionError("받는 중에 연결이 끊겼습니다.")
                f.write(chunk)
                if halt.is_set():
                    raise Halt(collect_http.STOPPED, failed=False)
                with guard:
                    if info.get("ranges"):
                        record["done"] = merge(record["done"] + [[position, position + len(chunk)]])
                        record["bytes"] = sum(e - s for s, e in record["done"])
                    else:
                        record["bytes"] = position + len(chunk)
                    status.update(bytes=record["bytes"])
                    if time.monotonic() - last_save > SAVE_EVERY:
                        f.flush()
                        write_record(found["record"], record)
                        last_save = time.monotonic()
                position += len(chunk)
            f.flush()
        with guard:
            write_record(found["record"], record)

    def probe():
        """첫 요청은 Range: bytes=0-0으로 보내 범위 받기를 지원하는지·전체 크기·ETag·Last-Modified를 본다.
        200이면 범위를 모르는 서버라 그 응답을 그대로 한 줄기로 받는다."""
        reply, url = collect_http.open_url("GET", sent["url"], sent["headers"] + [["Range", "bytes=0-0"]], None, timeout, hide)
        with reply:
            headers = list(reply.headers.items())
            if reply.status not in (200, 206):
                return Reply(reply.status, headers, reply.read(4096), url)
            start, size = content_range(reply.headers.get("Content-Range"))
            info.update(url=url, etag=reply.headers.get("ETag"), modified=reply.headers.get("Last-Modified"), type=reply.headers.get("Content-Type"))
            if reply.status == 206 and start == 0 and size is not None:
                info.update(ranges=True, size=size)
                reply.read()
            else:  # 범위를 지원하지 않거나 크기를 모름: 처음부터 한 줄기로
                length = reply.headers.get("Content-Length")
                info.update(ranges=False, size=int(length) if reply.status == 200 and length and length.isdigit() else None)
                need_space(path.parent, info["size"] * (2 if converting else 1) if info["size"] else None)
                with guard:
                    record.update(done=[], bytes=0, size=info["size"], etag=None, modified=None)
                    status.update(total_bytes=info["size"], bytes=0)
                raw.write_bytes(b"")
                if reply.status == 206:  # 크기를 모르는 206: 범위 없이 다시 받는다.
                    return Reply(299, headers, b"", url)
                stream(reply, raw, 0, None)
        return Reply(reply.status, headers, b"", url)

    first = balancer.call(probe)
    if first.status == 299:
        def whole():
            reply, url = collect_http.open_url("GET", sent["url"], sent["headers"], None, timeout, hide)
            with reply:
                if reply.status == 200:
                    raw.write_bytes(b"")
                    stream(reply, raw, 0, None)
                    return Reply(200, list(reply.headers.items()), b"", url)
                return Reply(reply.status, list(reply.headers.items()), reply.read(4096), url)
        balancer.call(whole)
    if info["ranges"]:
        size = info["size"]
        same = record.get("size") == size and record.get("etag") == info["etag"] and record.get("modified") == info["modified"] and raw.is_file() and raw.stat().st_size == size
        if not same:  # 처음이거나 파일이 바뀜: 처음부터
            with guard:
                record.update(done=[], bytes=0, size=size, etag=info["etag"], modified=info["modified"])
                write_record(found["record"], record)
            need_space(path.parent, size * (2 if converting else 1))
            with open(raw, "wb") as f:
                f.truncate(size)  # 미리 크기만큼 잡아 두고 조각마다 제자리에 쓴다.
        else:
            update(message="이어 받습니다.")
        update(total_bytes=size, bytes=record["bytes"])
        piece = max(1, math.ceil(size / spec["chunks"]))
        todo = [[s + piece * k, min(e, s + piece * (k + 1))] for s, e in gaps(record["done"], size) for k in range(math.ceil((e - s) / piece))]
        errors, quit = [], threading.Event()
        if_range = info["etag"] or info["modified"]

        def chunk(start, end):
            def attempt():
                with guard:
                    left = gaps([r for r in record["done"]], size)
                position = next((max(s, start) for s, e in left if e > start and s < end), end)
                if position >= end:
                    return Reply(206, [], b"", info["url"])
                headers = sent["headers"] + [["Range", f"bytes={position}-{end - 1}"]] + ([["If-Range", if_range]] if if_range else [])
                reply, url = collect_http.open_url("GET", info["url"], headers, None, timeout, hide)
                with reply:
                    if reply.status == 200:  # 파일이 바뀌었거나 범위를 더는 받지 않음: 다음에는 처음부터
                        with guard:
                            record.update(done=[], bytes=0, size=None)
                            write_record(found["record"], record)
                        raise Halt("받는 중에 파일이 바뀌었습니다. 다시 시작하면 처음부터 받습니다.")
                    if reply.status != 206:
                        return Reply(reply.status, list(reply.headers.items()), reply.read(4096), url)
                    if content_range(reply.headers.get("Content-Range"))[0] != position:
                        raise ConnectionError("요청한 범위와 다른 범위가 왔습니다.")
                    stream(reply, raw, position, end)
                    return Reply(206, list(reply.headers.items()), b"", url)
            return attempt

        def worker(start, end):
            try:
                if not quit.is_set():
                    balancer.call(chunk(start, end))
            except Exception as e:
                errors.append(e)
                quit.set()
                halt.set()

        threads = [threading.Thread(target=worker, args=(s, e), daemon=True) for s, e in todo]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        if errors:
            raise min(errors, key=lambda e: not getattr(e, "failed", True))
        if gaps(record["done"], size):
            raise Halt("다 받지 못했습니다. 다시 시작하면 이어 받습니다.", failed=False)
    got = raw.stat().st_size
    if info.get("size") is not None and got != info["size"]:
        raise Halt(f"받은 크기({got:,}바이트)가 서버가 알려 준 크기({info['size']:,}바이트)와 다릅니다. 다시 시작하세요.")
    finish_file(config, path, found, info)


def output_kind(config, url, content_type, head=b""):
    """받은 파일의 형식: 정한 것, 아니면 주소의 확장자·Content-Type·앞 바이트로."""
    kind = config["file"]["kind"]
    if kind != "auto":
        return kind
    suffix = Path(urlsplit(url).path).suffix.lower()
    if head.startswith(b"PAR1") or suffix == ".parquet" or "parquet" in (content_type or ""):
        return "parquet"
    if suffix in (".json", ".jsonl", ".ndjson") or "json" in (content_type or ""):
        return "json"
    return "csv"


def is_utf8(path):
    decoder = codecs.getincrementaldecoder("utf-8")()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                decoder.decode(chunk)
        decoder.decode(b"", final=True)
        return True
    except UnicodeDecodeError:
        return False


def finish_file(config, path, found, info):
    """받은 파일이 실제로 열리는지 DuckDB로 확인하고, 인코딩·형식을 결과에 맞게 바꿔 결과 파일로 바꿔치기한다."""
    update(phase="바꾸는 중", wait="")
    raw, fmt = found["part"], config["output"]["format"]
    with open(raw, "rb") as f:
        head = f.read(4)
    kind = output_kind(config, info.get("url", ""), info.get("type"), head)
    encoding = config["file"]["encoding"]
    cp949 = kind != "parquet" and (encoding == "cp949" or (encoding == "auto" and not is_utf8(raw)))
    con, folder = duck()
    try:
        readable = raw
        if cp949 and kind != fmt:  # DuckDB는 UTF-8만 읽으므로 먼저 바꾼다.
            readable = Path(folder, "utf8")
            with_bom(raw, readable, "cp949")
        if kind == fmt == "csv":
            with_bom(raw, found["out"], "cp949" if cp949 else None)
            readable = found["out"]
        read = {"csv": con.read_csv, "parquet": con.read_parquet, "json": con.read_json}[kind]
        try:
            table = read(str(readable))
            rows = con.execute(table.count())
        except Exception as e:
            raise Halt(f"받은 파일을 {kind}로 열 수 없습니다: {str(e)[:300]}") from None
        if kind == fmt == "parquet":
            os.replace(raw, found["out"])
        elif kind != fmt:
            rows = write_output(con, table, fmt, found["out"], folder)
    finally:
        con.disconnect()
        scratch.remove(folder)
    replace(found, path)
    update(rows=rows)


def estimate(config):
    """예상 요청 수, 오늘 남은 횟수, 이어 받을 수 있는 것."""
    config = normalize(config)
    record = resumable(config)
    paging = config["paging"]
    if config["mode"] == "web":  # 쪽 하나를 여는 것이 요청 하나. 다음 버튼·스크롤은 이어 받을 때도 처음부터 다시 넘긴다.
        web = config["web"]["paging"]
        requests = 1 if web["kind"] == "none" else web["max_pages"]
    elif config["mode"] == "file":
        requests = None
    elif paging["kind"] == "none":
        requests = 1
    elif paging["kind"] in ("page", "offset"):
        requests = page_cap(config, record or {})
    else:  # cursor·next_url은 받아 보기 전에는 모른다.
        requests = None
    if requests is not None and record and not (config["mode"] == "web" and config["web"]["paging"]["kind"] in ("next", "scroll")):
        requests = max(0, requests - len([i for i in record["done"] if i < requests]))
    usage = collect_limits.usage(config["limit_group"]) if config["limit_group"] else None
    resume = None
    if record:
        resume = {"pages": len(record["done"]) if config["mode"] != "file" else 0, "bytes": record.get("bytes", 0)}
    found = {"requests": requests, "day_left": usage["day_left"] if usage else None, "resume": resume}
    if config["mode"] == "web" and config["web"]["robots"] and config["web"]["url"].strip():
        found["robots"] = collect_web.robots_text(config["web"])
    note = load_job.note_for(config, output_path(config) if config["output"]["path"].strip() else None)
    if note:
        found["load_note"] = note
    return found
