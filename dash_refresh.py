"""대시보드 최신화(2단계): 연결에 성공한 뒤 주기가 지난 카드를 작업 스레드 하나가 차례로 다시 구해 저장한다. 최신화 주기·실시간 간격 검사와 연결별 실시간 간격 최소도 여기 있다.
주기 글자는 period.parse_every가 읽는다(숫자 + 초·분·시간·일·주·달·개월·년). 최신화 주기는 MIN_REFRESH(1시간) 이상, 실시간 간격의 최소는 연결(source_key)마다 사용자가 정한다(설정 live_min.json, 없으면 제한 없음).
카드 하나씩 화면 요청과 같은 차례(core.using)를 잡고 dash_calc.compute로 구해 dash_store.set_card_data로 쓴다. 실패한 카드는 이전 값을 두고 이유만 남긴다."""
import datetime as dt
import threading

import core
import dash_calc
import dash_store
import period
import server
import settings
import source

MIN_REFRESH = 3600  # 최신화 주기의 최소(초)
LIVE_FILE = "live_min.json"  # {source_key: 실시간 간격 최소(초)}
MAX_LIVE_MIN = 86400
REFRESHABLE = ("chart", "analysis", "metric")  # 지금 다시 구할 수 있는 카드 종류
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
    return {k: v for k, v in found.items() if isinstance(k, str) and type(v) is int and 0 < v <= MAX_LIVE_MIN}


def live_min(key):
    return live_mins().get(key, 0)


def set_live_min(key, seconds):
    """이 연결(key)의 실시간 간격 최소를 정한다. 0이면 지운다(제한 없음)."""
    if not isinstance(key, str) or not key:
        raise ValueError("먼저 데이터에 연결하세요.")
    if type(seconds) is not int or not 0 <= seconds <= MAX_LIVE_MIN:
        raise ValueError(f"실시간 간격 최소는 0~{MAX_LIVE_MIN}초의 정수입니다(0이면 제한 없음).")
    found = live_mins()
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
                    data = dash_calc.compute(dash_store.check_card({**card, "data": {}}), now.isoformat(timespec="seconds"))
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
