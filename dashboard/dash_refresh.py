"""기간·비교 기간(context), 처리 용량 판정, 계산 방식 DB의 갱신 주기 최소."""
import datetime as dt

from dashboard import dash_calc
from core import dash_store
from prep import prep_app
from core import held
from dashboard import period
from core import scan_size
from core import server
from core import settings
from core.server import run

REMOTE_LIVE_MIN = 60  # 최소를 정하지 않은 원격 연결(DB 서버)의 갱신 주기 최소(초): 짧은 간격으로 서버를 두드리지 않게


def minimum_text(seconds):
    """초를 '1시간'·'30분'·'45초' 글자로."""
    for size, unit in ((3600, "시간"), (60, "분")):
        if seconds % size == 0:
            return f"{seconds // size}{unit}"
    return f"{seconds}초"


def live_min(key):
    """이 연결의 갱신 주기 최소(초): 원격 연결은 REMOTE_LIVE_MIN, 이 PC 파일(폴더·sqlite·duckdb)은 0(제한 없음)."""
    key = dash_store.canon_key(key)
    local = not isinstance(key, str) or not key.startswith("url:") or key.startswith(tuple(f"url:{s}://" for s in dash_store.FILE_SCHEMES))
    return 0 if local else REMOTE_LIVE_MIN


def check_pull_every(d: dict) -> None:
    """[SPECIFICATION] plans/dash-blocks.md#check_pull_every
    저장하려는 대시보드의 갱신 주기를 검사한다: 계산 방식 db면 카드가 쓰는 연결마다 연결별 최소(live_min) 이상이어야 한다."""
    pull = dash_store.check_pull(d.get("pull"))
    every = pull["every"]
    if pull["calc"] != "db" or every is None:
        return
    seconds = every["n"] * dash_store.PULL_UNITS[every["unit"]]
    keys = {(c.get("source") or {}).get("key") for c in d.get("cards") or [] if isinstance(c, dict)} - {None, ""}
    for key in sorted(keys):
        least = live_min(key)
        if seconds < least:
            label = next((c["source"].get("label") for c in d["cards"] if c["source"].get("key") == key and c["source"].get("label")), key)
            raise ValueError(f"‘{label}’ 연결의 갱신 주기는 {minimum_text(least)} 이상이어야 해요(계산 방식 DB).")


def timed(span):
    """시각(HH:MM)이 든 기간: 대시보드는 날짜 단위로만 구한다(시각을 자르면 직전 기간이 같은 날이 된다)."""
    return any(len(x or "") > 10 for x in (span["start"], span["end"]))


def context(d, today=None):
    """대시보드의 보는 기간·비교 기간 {range: {start, end}, prev: {start, end, text}}(기준일 as_of로 자름). 읽을 수 없으면 None.
    비교 기간은 대시보드의 비교 기준 compare_mode(period.MODES, 없으면 prev=직전 기간)로 period.previous가 정한다.
    기간이 비었어도 기준일을 정했으면 {as_of}만(문장 카드의 기준일 빈칸)."""
    if not (d.get("period") or "").strip():
        mode = {"compare_mode": d["compare_mode"]} if d.get("compare_mode") not in (None, "", "prev") else {}  # 카드 자신의 기간 줄(dash_calc.own_range)도 같은 비교 기준으로
        return {**({"as_of": d["as_of"]} if d.get("as_of") else {}), **mode} or None  # 기간이 비면 대시보드의 기간을 걸지 않는다(카드의 원래 조건대로)
    day = (d.get("as_of") or "") or (today or dt.date.today()).isoformat()  # 기준일(비면 오늘)
    mode = d.get("compare_mode") or "prev"
    span = period.parse(d.get("period"), day)
    if span["error"] or timed(span):
        return None
    end = min(e for e in (span["end"] and span["end"][:10], day) if e)
    if span["start"] and day < span["start"][:10]:
        return {"range": {"start": span["start"][:10], "end": end}, "prev": None}  # 기준일이 기간이 시작되기 전: 보는 기간은 비었고 견줄 직전 기간도 없다
    cut = span["start"] and span["end"] and span["start"][:10] <= day < span["end"][:10]  # 기준일이 기간 안이라 잘렸으면 직전 기간도 잘라 맞춘다
    aligned = None
    if cut:
        first, last, today = (dt.date.fromisoformat(x[:10]) for x in (span["start"], span["end"], day))
        aligned = period.month_count(first, last)
        if mode == "prev" and (aligned or (first.day == 1 and (first.year, first.month) == (today.year, today.month))):  # 달 단위로 꼭 맞는 기간(월·분기·연…), 또는 1일에 시작해 기준일이 그 달 안: 직전 같은 개월 수의 같은 위치까지
            months = aligned or 1
            label = period.TO_DATE_BEFORE["month"] if months == 1 else f"{period.month_name(months, first)} 같은 기간"  # 한 달은 풀어 쓴 "지난달 같은 기간"(딱 한 달 이름 "전월"과 따로)
            try:
                before = {"start": period.add_months(first, -months).isoformat(), "end": period.add_months(today, -months).isoformat(), "label": label, "error": None}
            except ValueError as e:  # 1년 1월처럼 직전 기간이 달력 앞이면 견줄 기간 없이 본다
                before = {"error": str(e)}
            span = {**span, "to_date": True}
        else:  # 그 밖의 기간은 같은 일수만큼 바로 앞(같은 요일·작년 비교는 기준일까지 자른 기간을 옮긴다)
            span = {**span, "end": day, "to_date": None if mode == "prev" else span.get("to_date") or ("month" if aligned else None)}  # 달에 맞는 기간은 작년 같은 달로
            before = period.previous(span, mode)
    else:
        before = period.previous(span, mode)
    first = None if before["error"] else dt.date.fromisoformat(before["start"][:10])
    months = None if before["error"] or span.get("to_date") else period.month_count(first, dt.date.fromisoformat(before["end"][:10]))
    prev = None if before["error"] else {"start": before["start"][:10], "end": before["end"][:10],
                                         "text": before["label"] if span.get("to_date") or mode != "prev" else period.month_name(months, first) if months else f"직전 {before['days']}일"}
    return {"range": {"start": span["start"] and span["start"][:10], "end": end}, "prev": prev, **({"months": aligned} if cut else {})}  # months: 잘린 기간의 원래 개월 수(달에 맞지 않으면 None) → 목표 달


def geo_sqls(card, con):
    """analysis·funnel 카드의 recipe에 좌표(geo) 단계가 있고 원격 연결이면, 아직 받아 두지 않은 지점·영역 표를 읽는 SQL들(prep_app.geo_gate와 같은 대상)."""
    recipe = card["source"].get("recipe") or {}
    if card["kind"] not in ("analysis", "funnel") or not recipe.get("geo") or not held.remote(con):
        return []
    from geo import prep_geo  # 좌표 단계가 있을 때만 올린다
    return [server.expr_sql(con, expr)[0] for key, expr in run(lambda: prep_geo.reference_exprs(prep_geo.check(recipe["geo"]), con)) if not held.has(key)]


def copy_sqls(card, con):
    """원격 연결에서 full 분석·흐름 카드가 아직 없는 작업 사본(prep_app.working_copy)을 새로 받아야 하면, 그 사본이 읽을 표의 SQL(prep_app.copy_gate와 같은 대상)."""
    source = card["source"]
    recipe = source.get("recipe") or {}
    if card["kind"] not in ("analysis", "funnel") or source.get("full") is not True or not recipe or not held.remote(con) or not prep_app.local_mode(recipe) or held.has(prep_app.cached_key(recipe)):
        return []
    return [run(lambda: prep_app.scan_sql(recipe))[0]]


def scan_decision(card, ctx):
    """이 카드를 읽기 전 처리 용량 판정(8-3). 건너뛸 이유 글(괜찮으면 빈 글자). 판정은 scan_found."""
    return scan_found(card, ctx).get("text", "")


def scan_found(card, ctx):
    """scan_decision의 판정: 멈출 까닭이 있으면 {over, blocked, text(건너뛸 이유 글), …}, 없으면 {}. 대시보드 카드 구하기 라우트는 이것으로 묻는다(X-Needs-Confirm).
    scan_size.limit_check(con, sql, opened, dialect)가 돌려주는 {over, unknown, blocked, bytes, limit, text}를 본다(그 함수가 없으면 막지 않는다).
    기준을 넘으면(over), 추정할 수 없는데 설정 scan_unknown이 block이면(blocked) 그 이유로 건너뛴다. 최신화는 묻지 않는다(그 카드만 건너뜀).
    DB를 묻는 판정이라 연결 스레드(server.run)에서 부른다. 식을 만들 수 없거나 판정이 실패하면 추정 불가로 보고 설정을 따른다."""
    check = getattr(scan_size, "limit_check", None)
    if check is None:
        return {}
    try:
        sql = dash_calc.read_sql(card, ctx)
        con = server.state["con"]
        sqls = [sql] if sql is not None else copy_sqls(card, con) + geo_sqls(card, con)  # 분석 카드는 식이 없어도 좌표 단계의 지점·영역 표를 통째로 받는다
        if not sqls:
            return {}
        if sql is not None and held.remote(con) and getattr(con, "name", "") != "bigquery" and not dash_calc.copies_to_pc(card, ctx):
            return {}  # 원격 DB에서 DB 안의 집계로 구하는 카드는 결과만 오므로 작다. 크기를 알아보려고 DB에 묻지 않는다(요금). BigQuery는 집계도 읽는 양에 요금이 나서 묻는다
        found = {}
        for one in sqls:
            found = run(lambda one=one: check(con, one, server.state["opened"], scan_size.files_dialect(con)))
            if found.get("over") or found.get("blocked"):
                break
    except Exception as e:
        found = {"over": False, "blocked": scan_size.blocks_unknown(settings.load()), "text": f"처리 용량을 알 수 없어요 ({e})"}
    if found.get("over") or found.get("blocked"):
        note = " 고유 개수는 기간별로 따로 읽어요." if held.remote(server.state["con"]) and dash_calc.unique_per_period(card, ctx) else ""  # 읽는 양이 이번 기간 + 직전 기간 몫이다
        return {**found, "text": (found.get("text") or "처리 용량이 기준을 넘거나 알 수 없어 건너뛰었어요") + note}
    return {}
