"""대시보드 최신화(2단계): 연결에 성공한 뒤 주기가 지난 카드를 작업 스레드 하나가 차례로 다시 구해 저장한다. 최신화 주기·실시간 간격 검사와 연결별 실시간 간격 최소도 여기 있다.
주기 글자는 period.parse_every가 읽는다(숫자 + 초·분·시간·일·주·달·개월·년). 최신화 주기는 MIN_REFRESH(1시간) 이상, 실시간 간격의 최소는 연결(source_key)마다 사용자가 정한다(설정 live_min.json, 없으면 제한 없음).
카드 하나씩 화면 요청과 같은 차례(querying.using)를 잡고 dash_calc.compute로 구해 dash_store.set_cards_data로 대시보드마다 한 번에 쓴다. 실패한 카드는 이전 값을 두고 이유만 남긴다."""
import datetime as dt
import threading
import time

from core import querying
from dashboard import dash_calc
from core import dash_store
from prep import prep_app
from core import held
from dashboard import period
from core import scan_size
from core import server
from core import settings
from core import source
from core import use_counts
from core.server import run

MIN_REFRESH = dash_store.MIN_REFRESH  # 최신화 주기의 최소(초)
LIVE_FILE = "live_min.json"  # {source_key: 실시간 간격 최소(초)}
MAX_LIVE_MIN = 86400
FLUSH_SECONDS = 30  # 한 대시보드를 구하는 동안 이만큼 지나면 그때까지 구한 카드를 먼저 저장한다(중간에 꺼져도 다 잃지 않게)
REFRESHABLE = ("chart", "analysis", "funnel", "metric", "kpi", "donut_kpi", "gauge", "summary", "dual_trend", "stripes", "blocks", "target_trend", "table", "status", "sentence", "alerts", "map", "deviation", "geomap")  # 지금 다시 구할 수 있는 카드 종류
JOB = {"running": False, "total": 0, "done": 0, "current": "", "results": []}  # 마지막 최신화 작업의 상태(status가 복사해 준다)
LOCK = threading.Lock()
STOP = threading.Event()
THREAD = [None]


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
    with settings.updating(LIVE_FILE):
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
    if span["start"] and day < span["start"][:10]:
        return {"range": {"start": span["start"][:10], "end": end}, "prev": None}  # 기준일이 기간이 시작되기 전: 보는 기간은 비었고 견줄 직전 기간도 없다
    cut = span["start"] and span["end"] and span["start"][:10] <= day < span["end"][:10]  # 기준일이 기간 안이라 잘렸으면 직전 기간도 잘라 맞춘다
    if cut:
        first, last, today = (dt.date.fromisoformat(x[:10]) for x in (span["start"], span["end"], day))
        aligned = first.day == 1 and last == period.month_end(last.year, last.month)
        if aligned or (first.day == 1 and (first.year, first.month) == (today.year, today.month)):  # 달 단위로 꼭 맞는 기간(월·분기·연…), 또는 1일에 시작해 기준일이 그 달 안: 직전 같은 개월 수의 같은 위치까지
            months = (last.year - first.year) * 12 + last.month - first.month + 1 if aligned else 1
            start = period.add_months(first, -months)
            name = {1: "지난달", 3: "직전 분기", 12: "작년"}.get(months, f"직전 {months}개월")
            before = {"start": start.isoformat(), "end": period.add_months(today, -months).isoformat(), "label": f"{name} 같은 기간", "error": None}
            span = {**span, "to_date": True}
        else:  # 그 밖의 기간은 같은 일수만큼 바로 앞
            span = {**span, "end": day, "to_date": None}
            before = period.previous(span)
    else:
        before = period.previous(span)
    prev = None if before["error"] else {"start": before["start"][:10], "end": before["end"][:10], "text": before["label"] if span.get("to_date") else f"직전 {before['days']}일"}
    return {"range": {"start": span["start"] and span["start"][:10], "end": end}, "prev": prev}


SCAN_BLOCK = ("차단", "block")  # 설정 scan_unknown의 값: 추정할 수 없을 때 막기


def geo_sqls(card, con):
    """analysis·funnel 카드의 recipe에 좌표(geo) 단계가 있고 원격 연결이면, 아직 받아 두지 않은 지점·영역 표를 읽는 SQL들(prep_app.geo_gate와 같은 대상)."""
    recipe = card["source"].get("recipe") or {}
    if card["kind"] not in ("analysis", "funnel") or not recipe.get("geo") or not held.remote(con):
        return []
    from geo import prep_geo  # 좌표 단계가 있을 때만 올린다
    return [server.expr_sql(con, expr)[0] for key, expr in run(lambda: prep_geo.reference_exprs(prep_geo.check(recipe["geo"]), con)) if key not in held.store["items"]]


def copy_sqls(card, con):
    """원격 연결에서 full 분석·흐름 카드가 아직 없는 작업 사본(prep_app.working_copy)을 새로 받아야 하면, 그 사본이 읽을 표의 SQL(prep_app.copy_gate와 같은 대상)."""
    source = card["source"]
    recipe = source.get("recipe") or {}
    if card["kind"] not in ("analysis", "funnel") or source.get("full") is not True or not recipe or not held.remote(con) or not prep_app.local_mode(recipe) or prep_app.copy_key(recipe) in held.store["items"]:
        return []
    return [run(lambda: prep_app.scan_sql(recipe))[0]]


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
        con = server.state["con"]
        sqls = [sql] if sql is not None else copy_sqls(card, con) + geo_sqls(card, con)  # 분석 카드는 식이 없어도 좌표 단계의 지점·영역 표를 통째로 받는다
        if not sqls:
            return ""
        if sql is not None and held.remote(con) and not dash_calc.copies_to_pc(card, ctx):
            return ""  # 원격 DB에서 DB 안의 집계로 구하는 카드는 결과만 오므로 작다. 크기를 알아보려고 DB에 묻지 않는다(요금)
        dialect = "duckdb" if getattr(con, "name", "") == "polars" else None  # 파일 연결은 duckdb 방언으로(scan 라우트와 같다)
        found = {}
        for one in sqls:
            found = run(lambda one=one: check(con, one, server.state["opened"], dialect))
            if found.get("over") or found.get("blocked"):
                break
    except Exception as e:
        found = {"over": False, "blocked": settings.load().get("scan_unknown") in SCAN_BLOCK, "text": f"처리 용량을 알 수 없어요 ({e})"}
    if found.get("over") or found.get("blocked"):
        note = " 고유 개수는 기간별로 따로 읽어요." if held.remote(server.state["con"]) and dash_calc.unique_per_period(card, ctx) else ""  # 읽는 양이 이번 기간 + 직전 기간 몫이다
        return (found.get("text") or "처리 용량이 기준을 넘거나 알 수 없어 건너뛰었어요") + note
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
    if not use_counts.same_source((card.get("source") or {}).get("key"), key):
        return "다른 연결에서 구한 카드예요"
    done = parse_time((card.get("data") or {}).get("computed_at"))
    if done is not None and (now - done).total_seconds() < every:
        return "아직 주기가 지나지 않았어요"
    return ""


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
    저장한 대시보드마다 주기가 있는 것만 보며, 이번에 안 구하는 카드는 이유와 함께 건너뜀으로 적는다. 돌고 있는지 확인하는 데서 작업 상태를 쓰는 데까지 한 잠금 안이라 동시에 두 번 눌러도 한 번만 시작한다."""
    now_source = dash_store.current_source()
    if now_source is None:
        raise ValueError("먼저 데이터에 연결하세요.")
    now = now or dt.datetime.now()
    with LOCK:
        if JOB["running"]:
            return {**JOB, "results": [dict(r) for r in JOB["results"]]}
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
        JOB.update(running=bool(plan), total=len(plan), done=0, current="", results=results)
        if plan:
            THREAD[0] = threading.Thread(target=work, args=(plan, now), daemon=True)
            THREAD[0].start()
    return status()


def work(plan, now):
    """작업 스레드: 카드를 차례로 구한다. 멈추기를 누르면 남은 카드는 건너뜀. 끝나면 running을 끈다.
    대시보드마다 보는 기간과 같은 테이블·조건을 묶은 읽기 계획은 그 대시보드의 첫 카드를 구할 때 세운다(실패하면 그 카드만 실패로 남는다)."""
    try:
        contexts, plans, mine, pending = {}, {}, {}, {}  # pending: 대시보드 id → [(slot, 카드 id, 값, 같은지 확인)]. 한 대시보드를 다 구한 뒤 저장소를 한 번만 쓴다

        last = [time.monotonic()]  # 마지막으로 저장한 때

        def flush(dash_id):
            last[0] = time.monotonic()
            items = pending.pop(dash_id, [])
            if items:
                try:
                    saved = dash_store.set_cards_data(dash_id, [(c, v, s) for _, c, v, s in items])
                except Exception as e:
                    saved = [e] * len(items)
                for (slot, *_), got in zip(items, saved):
                    # 구하는 동안 사용자가 조회를 고쳐 저장했으면(None) 옛 정의로 구한 값을 새 정의에 붙이지 않는다
                    outcome = ("failed", source.mask(str(got), server.state["passwords"])) if isinstance(got, Exception) else (
                        ("skipped", "구하는 동안 카드를 고쳤어요. 다음에 다시 구해요") if got is None else ("failed", "그사이 지운 카드예요") if not got else ("ok", ""))
                    with LOCK:
                        slot.update(state=outcome[0], message=outcome[1])
        for d, card in plan:
            mine.setdefault(d["id"], []).append(card)
        renewed = False  # 최신화를 시작하고 첫 카드에서 한 번만 PC 사본을 새로 받게 한다
        for d, card in plan:
            for other in [k for k in pending if k != d["id"]]:
                flush(other)
            slot = next(r for r in JOB["results"] if r["dashboard"] == d["id"] and r["card"] == card["id"])
            if STOP.is_set():
                with LOCK:
                    slot.update(state="skipped", message="멈췄어요")
                continue
            with LOCK:
                JOB["current"] = card["title"]
            try:
                with querying.using(fresh=not renewed):
                    renewed = True
                    if d["id"] not in plans:
                        contexts[d["id"]] = context(d)
                        plans[d["id"]] = dash_calc.shared_context(mine[d["id"]], contexts[d["id"]])  # 같은 테이블·조건의 카드들을 묶는다
                    ctx = plans[d["id"]]
                    checked = dash_store.check_card({**card, "data": {k: v for k, v in (card.get("data") or {}).items() if k == "since"}})
                    why = scan_decision(checked, ctx)
                    if why:
                        outcome = ("skipped", why)
                    else:
                        data = dash_calc.compute(checked, now.isoformat(timespec="seconds"), ctx)
                        want = dash_store.def_hash(dash_calc.definition(checked))
                        pending.setdefault(d["id"], []).append((slot, card["id"], data, lambda c, want=want: dash_store.def_hash(dash_calc.definition(dash_store.check_card(c))) == want))
                        outcome = None  # 저장하며 정한다(flush)
            except Exception as e:  # 이전 값은 그대로 두고 이유만 남긴다
                outcome = ("failed", source.mask(str(e), server.state["passwords"]))
            with LOCK:
                if outcome:
                    slot.update(state=outcome[0], message=outcome[1])
                JOB["done"] += 1
            if time.monotonic() - last[0] >= FLUSH_SECONDS:
                flush(d["id"])
    finally:
        for k in list(pending):
            flush(k)
        with LOCK:
            JOB.update(running=False, current="")
