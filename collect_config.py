"""수집 구성(config)의 기본값·고를 수 있는 값·숫자 범위와 검사(normalize). 수집 작업(API·파일·웹)과 수집 화면·예약이 같이 쓴다.
입력·출력은 하지 않는다."""
import re

import collect_request as req
import load_job

VERSION = 1
COLLECTIONS = "collections.json"  # 저장한 수집 구성(수집 화면과 예약이 쓴다)
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
    # load_id: 행마다 적재 ID 컬럼(uploads.LOAD_ID)을 넣어 나중에 그 적재의 행만 고를 수 있게 한다.
    "load": {"target": "file", "table": {"connection": {"kind": "folder", "file": "", "url": "", "user": ""}, "schema": "", "name": "", "how": "new", "key": [], "load_id": False},
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
