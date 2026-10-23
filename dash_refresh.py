"""대시보드 최신화(2단계): 연결에 성공한 뒤 주기가 지난 카드를 작업 스레드 하나가 차례로 다시 구해 저장한다. 최신화 주기·실시간 간격 검사와 연결별 실시간 간격 최소도 여기 있다.
주기 글자는 period.parse_every가 읽는다(숫자 + 초·분·시간·일·주·달·개월·년). 최신화 주기는 MIN_REFRESH(1시간) 이상, 실시간 간격의 최소는 연결(source_key)마다 사용자가 정한다(설정 live_min.json, 없으면 제한 없음).
카드 하나씩 화면 요청과 같은 차례(core.using)를 잡고 dash_calc.compute로 구해 dash_store.set_card_data로 쓴다. 실패한 카드는 이전 값을 두고 이유만 남긴다."""
import datetime as dt
import threading

import core
import dash_calc
import dash_store
import held
import period
import scan_size
import server
import settings
import source
from server import run

MIN_REFRESH = 3600  # 최신화 주기의 최소(초)
LIVE_FILE = "live_min.json"  # {source_key: 실시간 간격 최소(초)}
MAX_LIVE_MIN = 86400
REFRESHABLE = ("chart", "analysis", "metric", "kpi", "donut_kpi", "gauge", "summary", "dual_trend", "stripes", "blocks", "target_trend", "table", "status", "sentence", "alerts", "map", "deviation")  # 지금 다시 구할 수 있는 카드 종류
JOB = {"running": False, "total": 0, "done": 0, "current": "", "results": []}  # 마지막 최신화 작업의 상태(status가 복사해 준다)
LOCK = threading.Lock()
STOP = threading.Event()
THREAD = [None]


def current_source():
    """지금 연결의 {key, label}(비밀번호·계정 이름 없음). 연결이 없으면 None."""
    if server.state["opened"] is None:
        return None
    recent = source.recent(server.state["opened"])
    return {"key": dash_store.source_key(recent), "label": dash_store.source_label(recent)}


def every_seconds(text, minimum, what):
    """주기 글자를 초로. minimum초보다 짧으면 ValueError. 비었으면 0(쓰지 않음)."""
    if not (text or "").strip():
        return 0
    seconds = period.parse_every(text)
    if seconds < minimum:
        raise ValueError(f"{what} 주기는 {minimum_text(minimum)} 이상이어야 해요. (‘{text.strip()}’)")
    return seconds


def minimum_text(seconds):
    """초를 '1시간'·'30분'·'45초' 글자로."""
    for size, unit in ((3600, "시간"), (60, "분")):
        if seconds % size == 0:
            return f"{seconds // size}{unit}"
    return f"{seconds}초"


def live_mins():
    """연결별 실시간 간격 최소 {source_key: 초}(잘못된 항목은 뺀다)."""
    found = settings.read(LIVE_FILE) or {}
    return {dash_store.canon_key(k): v for k, v in found.items() if isinstance(k, str) and type(v) is int and 0 < v <= MAX_LIVE_MIN}  # 예전 모양의 열쇠도 지금 모양으로


def live_min(key):
    return live_mins().get(dash_store.canon_key(key), 0)


def set_live_min(key, seconds):
    """이 연결(key)의 실시간 간격 최소를 정한다. 0이면 지운다(제한 없음)."""
    if not isinstance(key, str) or not key:
        raise ValueError("먼저 데이터에 연결하세요.")
    if type(seconds) is not int or not 0 <= seconds <= MAX_LIVE_MIN:
        raise ValueError(f"실시간 간격 최소는 0~{MAX_LIVE_MIN}초의 정수입니다(0이면 제한 없음).")
    found, key = live_mins(), dash_store.canon_key(key)
    if seconds:
        found[key] = seconds
    else:
        found.pop(key, None)
    settings.write(LIVE_FILE, found)
    return found


def check_dashboard_every(d):
    """저장하려는 대시보드의 최신화 주기(1시간 이상)와 실시간 간격(카드가 쓰는 연결들의 최소 이상)을 검사한다."""
    every_seconds((d.get("refresh") or {}).get("every"), MIN_REFRESH, "최신화")
    live = every_seconds((d.get("live") or {}).get("every"), 1, "실시간")
    keys = {(c.get("source") or {}).get("key") for c in d.get("cards") or [] if isinstance(c, dict)} - {None, ""}
    for key in sorted(keys):
        least = live_min(key)
        if live and live < least:
            label = next((c["source"].get("label") for c in d["cards"] if c["source"].get("key") == key and c["source"].get("label")), key)
            raise ValueError(f"‘{label}’ 연결의 실시간 간격은 {minimum_text(least)} 이상으로 정해 두었어요. 더 짧게 쓰려면 그 연결의 최소를 먼저 바꾸세요.")


def context(d, today=None):
    """대시보드의 보는 기간·직전 기간 {range: {start, end}, prev: {start, end, text}}(기준일 as_of로 자름). 읽을 수 없으면 None."""
    if not (d.get("period") or "").strip():
        return None  # 기간이 비면 대시보드의 기간을 걸지 않는다(카드의 원래 조건대로)
    day = (d.get("as_of") or "") or (today or dt.date.today()).isoformat()  # 기준일(비면 오늘)
    span = period.parse(d.get("period"), day)
    if span["error"]:
        return None
    end = min(e for e in (span["end"] and span["end"][:10], day) if e)
    before = period.previous(span)
    prev = None if before["error"] else {"start": before["start"][:10], "end": before["end"][:10], "text": before["label"] if span.get("to_date") else f"직전 {before['days']}일"}
    return {"range": {"start": span["start"] and span["start"][:10], "end": end}, "prev": prev}


SCAN_BLOCK = ("차단", "block")  # 설정 scan_unknown의 값: 추정할 수 없을 때 막기


def scan_decision(card, ctx):
    """이 카드를 읽기 전 처리 용량 판정(8-3). 건너뛸 이유 글(괜찮으면 빈 글자).
    scan_size.limit_check(con, sql, opened, dialect)가 돌려주는 {over, unknown, blocked, bytes, limit, text}를 본다(그 함수가 없으면 막지 않는다).
    기준을 넘으면(over), 추정할 수 없는데 설정 scan_unknown이 차단이면(blocked) 그 이유로 건너뛴다. 최신화는 묻지 않는다(그 카드만 건너뜀).
    DB를 묻는 판정이라 연결 스레드(server.run)에서 부른다. 식을 만들 수 없거나 판정이 실패하면 추정 불가로 보고 설정을 따른다."""
    check = getattr(scan_size, "limit_check", None)
    if check is None:
        return ""
    try:
        sql = dash_calc.read_sql(card, ctx)
        if sql is None:
            return ""
        con = server.state["con"]
        if held.remote(con) and not dash_calc.copies_to_pc(card, ctx):
            return ""  # 원격 DB에서 DB 안의 집계로 구하는 카드는 결과만 오므로 작다. 크기를 알아보려고 DB에 묻지 않는다(요금)
        found = run(lambda: check(con, sql, server.state["opened"], "duckdb" if getattr(con, "name", "") == "polars" else None))  # 파일 연결은 duckdb 방언으로(scan 라우트와 같다)
    except Exception as e:
        found = {"over": False, "blocked": settings.load().get("scan_unknown") in SCAN_BLOCK, "text": f"처리 용량을 알 수 없어요 ({e})"}
    if found.get("over") or found.get("blocked"):
        return found.get("text") or "처리 용량이 기준을 넘거나 알 수 없어 건너뛰었어요"
    return ""


def parse_time(text):
    try:
        return dt.datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None


def skip_reason(d, card, key, now):
    """이 카드를 이번에 다시 구하지 않는 이유(구하면 빈 글자)."""
    every = every_seconds((d.get("refresh") or {}).get("every"), 0, "최신화")
    if card["kind"] not in REFRESHABLE:
        return "아직 다시 구할 수 없는 종류예요"
    if (card.get("source") or {}).get("key") != key:
        return "다른 연결에서 구한 카드예요"
    done = parse_time((card.get("data") or {}).get("computed_at"))
    if done is not None and (now - done).total_seconds() < every:
        return "아직 주기가 지나지 않았어요"
    return ""


def due_cards(dashboards, key, now):
    """다시 구할 카드 [(대시보드, 카드)]: 최신화 주기가 있는 대시보드의 카드 중 연결 열쇠가 지금 연결(key)이고
    (구한 시각이 없거나 now − 구한 시각 ≥ 주기)인 것. now는 늘 실제 지금 시각이다(기준일과 무관)."""
    return [(d, c) for d in dashboards if (d.get("refresh") or {}).get("every") for c in d.get("cards", []) if not skip_reason(d, c, key, now)]


def status():
    """마지막 최신화 작업의 상태 {running, total, done, current, results: [{dashboard, card, title, state(ok·failed·skipped), message}]}."""
    with LOCK:
        return {**JOB, "results": [dict(r) for r in JOB["results"]]}


def stop():
    """돌고 있는 최신화를 멈춘다: 지금 카드는 취소를 요청하고 남은 카드는 건너뛴다."""
    STOP.set()
    if JOB["running"]:
        server.state["cancel"].set()
    return status()


def wait(timeout=30):
    """최신화 작업이 끝나기를 기다린다(시험용)."""
    thread = THREAD[0]
    if thread is not None:
        thread.join(timeout)


def result(d, card, state, message=""):
    return {"dashboard": d["id"], "card": card["id"], "title": card["title"], "state": state, "message": message}


def start(now=None):
    """지금 연결로 주기가 지난 카드의 최신화를 작업 스레드에서 시작한다. 이미 돌고 있으면 그 상태를 돌려준다. 연결이 없으면 오류.
    저장한 대시보드마다 주기가 있는 것만 보며, 이번에 안 구하는 카드는 이유와 함께 건너뜀으로 적는다."""
    now_source = current_source()
    if now_source is None:
        raise ValueError("먼저 데이터에 연결하세요.")
    with LOCK:
        if JOB["running"]:
            return {**JOB, "results": [dict(r) for r in JOB["results"]]}
    now = now or dt.datetime.now()
    dashboards = [d for d in dash_store.editable().values() if (d.get("refresh") or {}).get("every")]
    plan, results = [], []
    for d in sorted(dashboards, key=lambda x: x["name"]):
        for card in d["cards"]:
            why = skip_reason(d, card, now_source["key"], now)
            if why:
                results.append(result(d, card, "skipped", why))
            else:
                plan.append((d, card))
                results.append(result(d, card, "waiting"))
    STOP.clear()
    with LOCK:
        JOB.update(running=bool(plan), total=len(plan), done=0, current="", results=results)
    if plan:
        THREAD[0] = threading.Thread(target=work, args=(plan, now), daemon=True)
        THREAD[0].start()
    return status()


def work(plan, now):
    """작업 스레드: 카드를 차례로 구한다. 멈추기를 누르면 남은 카드는 건너뜀. 끝나면 running을 끈다."""
    try:
        contexts, shared = {}, {}  # 대시보드마다 보는 기간, 같은 테이블·조건의 카드가 함께 읽은 값
        for d, card in plan:
            contexts.setdefault(d["id"], context(d))
        plans = {}
        for d, card in plan:
            plans.setdefault(d["id"], []).append(dash_store.check_card({**card, "data": {}}))
        plans = {did: dash_calc.plan_shared(cards, contexts[did]) for did, cards in plans.items()}  # 같은 테이블·조건의 카드들을 묶는다
        for d, card in plan:
            slot = next(r for r in JOB["results"] if r["dashboard"] == d["id"] and r["card"] == card["id"])
            if STOP.is_set():
                with LOCK:
                    slot.update(state="skipped", message="멈췄어요")
                continue
            with LOCK:
                JOB["current"] = card["title"]
            try:
                with core.using():
                    checked = dash_store.check_card({**card, "data": {k: v for k, v in (card.get("data") or {}).items() if k == "since"}})
                    ctx = {**(contexts[d["id"]] or {}), "plan": plans[d["id"]], "shared": shared.setdefault(d["id"], {})}
                    why = scan_decision(checked, ctx)
                    if why:
                        outcome = ("skipped", why)
                    else:
                        data = dash_calc.compute(checked, now.isoformat(timespec="seconds"), ctx)
                        if not dash_store.set_card_data(d["id"], card["id"], data):
                            raise ValueError("그사이 지운 카드예요")
                        outcome = ("ok", "")
            except Exception as e:  # 이전 값은 그대로 두고 이유만 남긴다
                outcome = ("failed", source.mask(str(e), server.state["passwords"]))
            with LOCK:
                slot.update(state=outcome[0], message=outcome[1])
                JOB["done"] += 1
    finally:
        with LOCK:
            JOB.update(running=False, current="")
