"""수집 작업: 설정(config) 검사, 한 번에 하나씩 뒤에서 도는 작업(API 쪽 넘기기, 파일 조각 받기), 받은 것을 결과 파일로 바꾸기, 상태.
받는 것은 바로 디스크에 둔다: 결과 경로 옆 '이름.part/'(API, 쪽마다 JSON 줄 파일)나 '이름.part'(파일 모드), 진행 기록 '이름.part.json'.
같은 설정으로 다시 시작하면 이어 받는다. 지우는 것은 이 이름 규칙의 것뿐이다.
구성의 기본값·검사는 collect_config, 상태와 쪽 저장·결과 파일 만들기(웹페이지 방식과 같이 쓰는 것)는 collect_store에 있다."""
import codecs
import datetime as dt
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

from collect import collect_http
from collect import collect_limits
from collect import collect_request as req
from collect import collect_web
from core import filelock
from collect import load_job
from core import scratch
from core import server
from core import settings
from collect import uploads
from collect.collect_config import normalize
from collect.collect_http import Halt, Reply
from collect.collect_store import (IDLE, began, SAVE_EVERY, balancer_for, clean, current, duck, finish_api, guard, keep_record, replace, req_mask, save_page, status,
                           update, with_bom, write_output, write_record)

MIN_FREE = 200 * 1024 * 1024  # 시작할 때 결과 폴더의 디스크에 적어도 남아 있어야 하는 공간
RUNNING = "이미 수집이 돌고 있습니다. 끝나거나 멈춘 뒤에 시작하세요."
BUSY = "같은 결과 파일에 다른 곳(다른 창, 프로그램 안 예약, Windows 예약 작업)이 수집하고 있습니다. 끝난 뒤에 시작하세요."


class Busy(ValueError):
    """같은 결과 파일에 다른 곳이 수집하고 있다(잠금을 잡지 못함)."""


# ---------- 설정 ----------

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
    env = settings.read_env(config["env_path"]) if config["env_path"].strip() else {}
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


def today():
    return dt.date.today().isoformat()


def digest(config):
    """이어 받기를 정하는 설정의 해시. 동시 수·간격·최대 쪽 수·결과 형식처럼 받은 내용이 바뀌지 않는 칸은 넣지 않는다.
    주소·요청에 {today}·{now}가 있으면 오늘 날짜도 넣는다(어제 받다 만 것을 오늘 이어 받으면 날짜가 섞이므로)."""
    if config["mode"] == "api":
        paging = {k: v for k, v in config["paging"].items() if k != "max_pages"}
        key = {"mode": "api", "request": config["request"], "response": config["response"], "paging": paging}
    elif config["mode"] == "file":
        key = {"mode": "file", "url": config["file"]["url"], "headers": config["file"]["headers"]}
    else:  # 웹페이지: 최대 쪽 수·기다리기·robots·보이기는 받은 내용을 바꾸지 않는다.
        web = config["web"]
        key = {"mode": "web", "url": web["url"], "items": web["items"], "fields": web["fields"], "paging": {k: v for k, v in web["paging"].items() if k != "max_pages"}}
    text = json.dumps(key, sort_keys=True, ensure_ascii=False)
    if any(m[3] for m in req.PLACE.finditer(text)):
        text += today()
    return hashlib.sha256(text.encode()).hexdigest()


def read_record(path):
    try:
        found = json.loads(path.read_text(encoding="utf-8"))
        return found if isinstance(found, dict) and "hash" in found else None
    except (OSError, ValueError):
        return None


def resumable(config):
    """같은 설정으로 이어 받을 수 있는 진행 기록. 없으면 None."""
    if not config["output"]["path"].strip():
        return None
    record = read_record(parts(output_path(config))["record"])
    return record if record and record.get("hash") == digest(config) else None


# ---------- 상태 ----------

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


def lock_of(config):
    """이 구성의 결과 파일에 대한 잠금 파일 경로(작업 파일 .part의 경로가 열쇠 - 결과 형식만 다른 구성도 같은 .part를 쓰므로 한 잠금). 같은 결과 파일에 두 곳이 쓰지 않게 start·start_load가 잡는다."""
    return filelock.lock_path("collect:" + os.path.normcase(os.path.abspath(parts(output_path(config))["part"])))


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
    found, lock = parts(path), lock_of(config)
    if not filelock.hold(lock):
        raise Busy(BUSY)
    try:
        record = read_record(found["record"])
        resumed = bool(record and record.get("hash") == digest(config))
        if not resumed:
            if record is not None or found["part"].exists() or found["out"].exists():
                clean(found)
            record = {"hash": digest(config), "mode": config["mode"], "done": [], "rows": 0, "bytes": 0}
            write_record(found["record"], record)
        launch(config, env, path, found, record, password, lock=lock, pages=len(record["done"]) if config["mode"] != "file" else 0, rows=record.get("rows", 0),
               bytes=record.get("bytes", 0), total_bytes=record.get("size"), message="이어 받습니다." if resumed else "", group=group)
    except BaseException:
        filelock.let_go(lock)
        raise
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
    lock = lock_of(config)
    if not filelock.hold(lock):
        raise Busy(BUSY)
    try:
        launch(config, {}, path, None, None, password, lock=lock, phase="적재 중", output=str(path))
    except BaseException:
        filelock.let_go(lock)
        raise


def launch(config, env, path, found, record, password, lock=None, **shown):
    """상태를 처음으로 돌리고(shown으로 고친 칸만 다르게) 작업 스레드를 띄운다. lock은 작업이 끝날 때(work의 finally) 푼다."""
    halt = threading.Event()
    current.update(halt=halt, balancer=None, hide=req.hidden(env) + ([password] if password else []))
    update(**{**IDLE, "running": True, "phase": "받는 중", "mode": config["mode"], "started": time.time(), **shown})
    thread = threading.Thread(target=work, args=(config, env, path, found, record, halt, password, lock), daemon=True)
    current["thread"] = thread
    thread.start()


def work(config, env, path, found, record, halt, password, lock=None):
    """받기(record가 None이면 건너뜀) → 결과 파일이 있고 적재할 곳이 있으면 적재. 적재가 실패해도 결과 파일은 남는다.
    받기가 결과 파일을 새로 만들지 않았으면(받은 기록이 0건) 옛 결과 파일은 기록도 적재도 하지 않는다."""
    try:
        made = record is None  # 적재만 할 때는 있는 결과 파일을 쓴다
        if record is not None:
            made = RUNNERS[config["mode"]](config, env, path, found, record, halt)
            if made:
                uploads.log_file(path, load_job.total_rows(path))  # 업로드 기록: 저장한 결과 파일
        if config["load"]["target"] != "file" and made:
            update(phase="적재 중", output=str(path), wait="")
            loaded = load_job.run(config, path, password, halt, update)
            if loaded["failures"]:  # 넣지 못한 행이 있으면 끝났다고만 하지 않고 문구로 남긴다(예약 실행은 이것을 실패로 알린다)
                update(message=f"적재에서 실패 {loaded['failures']:,}행이 있습니다. 넣은 행은 그대로입니다.")
        update(phase="끝", output=str(path) if made else "", wait="")
    except Halt as e:
        update(phase="실패" if e.failed else "멈춤", **({"error": str(e)} if e.failed else {"message": str(e)}), wait="")
    except Exception as e:
        update(phase="실패", error=str(e) or type(e).__name__, wait="")
    finally:
        with guard:
            if record is not None and found["part"].exists():  # 끝나지 않은 받기(멈춤·실패): 이어 받기용 진행 기록을 지금 모습으로 마저 쓴다(끝났다고 알리기 전에)
                keep_record(found, record, now=True)
            balancer = current["balancer"]
            if balancer is not None:
                status.update(requests=balancer.sent, failures=balancer.failures, concurrency=balancer.limit)
            if lock is not None:  # 끝났다고 알리기 전에 푼다(알린 직후 다시 시작해도 잠겨 있지 않게)
                filelock.let_go(lock)
            status.update(running=False, elapsed=round(time.time() - status["started"], 1))
        current["hide"] = []  # 비밀번호·env 값은 작업이 끝나면 버린다.


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


def context(config, env, i, cursor="", now=None):
    paging = config["paging"]
    return {"env": env, "page": paging["start"] + i, "offset": paging["start"] + i * paging["step"], "cursor": cursor, "now": now or dt.datetime.now()}


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


def run_api(config, env, path, found, record, halt):
    paging, response = config["paging"], config["response"]
    found["part"].mkdir(exist_ok=True)
    balancer = balancer_for(config, halt, config["balance"]["concurrency"] if paging["kind"] in ("page", "offset") else 1)
    timeout, hide = config["balance"]["timeout"], current["hide"]
    record.setdefault("columns", [])
    record.setdefault("kinds", {})
    record.setdefault("rename", {})
    began(record)  # {today}를 시작 때 시각으로 고정(쪽 일꾼들이 나눠 정하지 않게 미리)

    def get(i, cursor="", url=None):
        sent = req.build(config["request"], context(config, env, i, cursor, began(record)), url)
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
            if paging["kind"] == "offset" and 0 < len(rows) < paging["step"]:  # 서버가 step보다 적게 주면 다음 offset(step 간격)에서 그 사이 행이 빠진다
                total, short = record.get("total"), len(rows)
                if total is None:  # 전체 수를 모르면 다음 쪽이 비지 않은지로 본다
                    i = claim()
                    if i is not None:
                        _, rows, reply = get(i)
                        with guard:
                            if not rows:
                                record["end"] = i
                        save_page(found, record, i, rows, len(reply.body))
                        total = short + 1 if rows else None
                if total is not None and total > short:
                    raise Halt(f"서버가 한 번에 {short}행만 줍니다. 한 번에 받는 수를 {short}으로 줄이세요.")
        threads = [threading.Thread(target=worker, daemon=True) for _ in range(max(1, balancer.limit))]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        if errors:
            raise min(errors, key=lambda e: not getattr(e, "failed", True))  # 실패가 있으면 그것을, 아니면 멈춤
    elif not record.get("finished"):  # cursor·next_url: 하나씩, 받은 커서·주소를 따라간다.
        order = record.setdefault("seen", [])  # 지나온 커서·주소(받은 순서). 쪽마다 정렬하지 않고 덧붙인다
        seen = set(order)

        def see(value):
            if value not in seen:
                seen.add(value)
                order.append(value)
        cursor, url = record.get("cursor", ""), record.get("next")
        origin = lambda u: (urlsplit(u).scheme.lower(), collect_http.host_of(u))  # 방식(http·https)과 호스트
        home = origin(req.build(config["request"], context(config, env, 0, "", began(record)))["url"])  # 처음 요청의 방식·호스트
        i = len(record["done"])
        while i < paging["max_pages"]:
            if paging["kind"] == "next_url" and i > 0 and not url:
                break
            data, rows, reply = get(i, cursor, url if paging["kind"] == "next_url" and i > 0 else None)
            if i == 0:
                note_first(data, rows)
                if paging["kind"] == "next_url":
                    see(reply.url)
            more = {}
            if paging["kind"] == "cursor":
                nxt = req.extract(data, paging["cursor_path"])
                nxt = None if nxt is None else str(nxt)
                end = not rows or nxt is None or nxt in seen
                more["cursor"] = nxt
            else:
                nxt = req.extract(data, paging["next_path"]) if paging["next_path"] else link_next(reply)
                nxt = None if nxt is None else urljoin(reply.url, str(nxt))
                if nxt is not None and origin(nxt) != home:  # 인증 헤더를 다른 서버나 암호화하지 않는 http로 보내지 않는다
                    raise Halt(f"다음 주소가 처음 요청과 다른 호스트·방식({urlsplit(nxt).scheme}://{urlsplit(nxt).hostname})을 가리켜 멈췄습니다. 믿을 수 있는 주소인지 확인하세요.")
                end = not rows or nxt is None or nxt in seen
                more["next"] = nxt
            total = record.get("total")
            end = end or (total is not None and record["rows"] + len(rows) >= total)
            if nxt is not None:
                see(nxt)
            save_page(found, record, i, rows, len(reply.body), finished=end, **more)
            if end:
                break
            cursor, url, i = more.get("cursor") or "", more.get("next"), i + 1
    return finish_api(config, path, found, record)


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
    sent = req.build({"method": "GET", "url": spec["url"], "headers": spec["headers"]}, context(config, env, 0, "", began(record)))
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
        same = (info["etag"] or info["modified"]) and record.get("size") == size and record.get("etag") == info["etag"] and record.get("modified") == info["modified"] and raw.is_file() and raw.stat().st_size == size  # 바뀜을 알아볼 값이 하나도 없으면, 받아 둔 파일이 잘렸으면 이어 받지 않는다
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
        validator = if_range(info["etag"], info["modified"])

        def chunk(start, end):
            def attempt():
                with guard:
                    left = gaps([r for r in record["done"]], size)
                position = next((max(s, start) for s, e in left if e > start and s < end), end)
                if position >= end:
                    return Reply(206, [], b"", info["url"])
                headers = collect_http.headers_for(info["url"], sent["url"], sent["headers"], hide) + [["Range", f"bytes={position}-{end - 1}"]] + ([["If-Range", validator]] if validator else [])
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
    return finish_file(config, path, found, info)


def if_range(etag, modified):
    """If-Range에 보낼 값: 강한 ETag, 약한 ETag(W/)면 Last-Modified(약한 것은 If-Range에 쓸 수 없다). 없으면 None."""
    return etag if etag and not etag.startswith("W/") else modified


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
        if cp949:  # DuckDB는 UTF-8만 읽으므로 먼저 바꾼다.
            readable = Path(folder, "utf8")
            with_bom(raw, readable, "cp949")
        read = {"csv": con.read_csv, "parquet": con.read_parquet, "json": con.read_json}[kind]
        try:
            table = read(str(readable))
            rows = con.execute(table.count())
        except Exception as e:
            raise Halt(f"받은 파일을 {kind}로 열 수 없습니다: {str(e)[:300]}") from None
        if kind == fmt == "parquet":
            os.replace(raw, found["out"])
        elif kind == fmt == "csv":  # 수식 막기를 하려고 다시 쓴다. 글자로 읽어 값의 모양(007, 1.10)은 그대로 두고, 글자로 읽힌 컬럼만 막는다
            texts = [name for name, t in table.schema().items() if t.is_string()]
            rows = write_output(con, con.read_csv(str(readable), all_varchar=True), fmt, found["out"], folder, texts)
        else:
            rows = write_output(con, table, fmt, found["out"], folder)
    finally:
        con.disconnect()
        scratch.remove(folder)
    replace(found, path)
    update(rows=rows)
    return True


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


RUNNERS = {"api": run_api, "file": run_file, "web": collect_web.run_web}  # 방식 → 받기. 결과 파일을 새로 만들었으면 True
