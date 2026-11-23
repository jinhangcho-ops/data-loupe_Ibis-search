"""대시보드의 라우트. server.app에 달린다. 저장·목록·지우기·카드 더하기·기간 읽기·비교 문장은 연결을 쓰지 않으므로 차례(turn)를 기다리지 않는다.
카드 값을 구하는 /api/dash/card/compute와 /api/dash/cards/compute만 연결에서 구한다(차례를 기다린다)."""
import datetime as dt
import time
from pathlib import Path

from flask import Response, jsonify, request

from lazy import Later, preload
from core import result
from core import server
from core import settings
from core import sql_funcs
from core import use_counts
from core.server import app


compare = Later("core.compare")
dash_calc = Later("dashboard.dash_calc")
dash_pdf = Later("dashboard.dash_pdf")
dash_pull = Later("dashboard.dash_pull")
dash_files = Later("dashboard.dash_files")
dash_keys = Later("dashboard.dash_keys")
dash_marks = Later("dashboard.dash_marks")
dash_refresh = Later("dashboard.dash_refresh")
dash_store = Later("core.dash_store")
held = Later("core.held")
kpi_hours = Later("dashboard.kpi_hours")
metric_file = Later("core.metric_file")
period = Later("dashboard.period")
querying = Later("core.querying")
scan_size = Later("core.scan_size")
targets = Later("core.targets")
viz_rules = Later("core.viz_rules")

IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".svg": "image/svg+xml"}  # 배치도 바탕 그림으로 내려줄 수 있는 것
MAX_IMAGE_BYTES = 8 * 1024 * 1024
PULL_HOWS = ("pull", "rewrite")  # 암호화를 바꾸는 방법: 다시 받기, 풀어서 다시 쓰기
MAX_DASH_ID = 32  # 요청의 대시보드 id 글자 수
MAX_FRESH_ID = 64  # 요청의 갱신 표(fresh) 글자 수
MAX_SLOT_ID = 16  # 받은 데이터 칸 id 글자 수(def_hash 16자)
DATE_CHARS = 10  # 날짜 글자 수(YYYY-MM-DD). 모양 검사는 refetch_range가 한다
TARGET_PAGE = 100 # 목표 파일의 문제 줄을 한 번에 돌려줄 수(화면은 [이전]/[다음]으로 넘겨 본다)
LAST = "dash_last.json"  # 첫 화면에서 마지막으로 연 대시보드 {id}
server.FREE.update({"/api/dash", "/api/dash/delete", "/api/dash/card/add", "/api/dash/settings", "/api/dash/last", "/api/dash/live_min",
                    "/api/period/parse", "/api/period/day", "/api/compare", "/api/targets", "/api/dash/image", "/dashboard", "/dashboard/build", "/api/dash/pdf", "/api/dash/templates", "/api/dash/templates/delete", "/api/dash/constants", "/chart", "/api/viz",
                    "/api/dash/pull/state", "/api/dash/pull/crypt", "/api/dash/pull/orphans", "/api/dash/pull/orphans/drop", "/api/dash/bell", "/api/dash/marks/set", "/api/dash/marks/save", "/api/dash/marks/load"})


def with_source(card):
    """카드의 source에 지금 연결의 key·label을 넣은 사본(연결이 없으면 그대로)."""
    now = dash_store.current_source()
    return {**card, "source": {**(card.get("source") or {}), **(now or {})}} if now else card


@server.late_constants("query", "prep", "dashboard")
def dash_constants(found):  # 카드 그리기(web/)와 차트 선반이 서버와 같은 상한·종류·기본 색·집계를 쓴다
    found["dash"] = {"max_cards": dash_store.MAX_CARDS, "max_dashboards": dash_store.MAX_DASHBOARDS, "max_palette": dash_store.MAX_PALETTE,
        "types": dash_store.CHART_TYPES, "time_types": dash_store.TIME_TYPES, "three_types": dash_store.THREE_D_TYPES, "units": dash_store.UNITS,
        "locked": dash_store.LOCKED}  # 화면(web/·common.js)이 서버와 같은 상한·종류·기본 색을 쓴다
    found["dash"]["max_points"] = result.MAX_POINTS  # 산점도·버블의 점 수 상한
    found["dash"]["max_filters"] = dash_store.MAX_FILTERS  # 대시보드 필터 칸 수 상한
    found["dash"]["max_pages"] = dash_store.MAX_PAGES  # 대시보드 쪽(탭) 수 상한
    found["dash"].update(max_bins=dash_store.MAX_BINS, max_bands=dash_store.MAX_BANDS, max_cells=dash_store.MAX_CELLS)  # 히스토그램의 구간 수, 불릿 차트의 구간 수, 히트맵 칸 수 상한
    found["dash"]["merge_note"] = dash_store.MERGE_NOTE  # (Phase 17) 범위 묶기 안내 한 줄(쿼리 차트 groupPanel·카드 GroupPicker가 같은 글을 보인다)
    found["dash"].update(aggs=list(metric_file.AGGS), merge=list(sql_funcs.MERGE), agg_names=metric_file.AGG_NAMES)  # 카드 계열의 집계(함수 사전의 집계 전부), 일 값을 다시 묶을 수 있는 집계, 보이는 이름
    found["dash"].update(auto=viz_rules.AUTO_PARAMS, auto_names=viz_rules.AUTO_NAMES, viz=viz_rules.load_prefs(), orders=viz_rules.ORDERS, order_names=viz_rules.ORDER_NAMES)  # 자동 규칙 수치표·이름, 차트 낱말과 쿼리 화면 기본값, 정렬 다섯 값·이름
    found["dash"]["pull"] = {"calcs": list(dash_store.PULL_CALCS), "ranges": list(dash_store.PULL_RANGES), "crypts": list(dash_store.PULL_CRYPTS), "units": dash_store.PULL_UNITS, "unit_names": dash_store.PULL_UNIT_NAMES, "min_every": dash_store.MIN_PULL_EVERY, "max_every": dash_store.MAX_PULL_EVERY, "default": dash_store.check_pull(None), "gap_note": dash_store.GAP_NOTE}  # 받기 설정 칸(계산 방식·갱신 주기·받는 범위·암호화)의 선택지와 상한
    found["dash"]["list"] = {"max_parts": dash_store.MAX_LIST_PARTS, "max_swaps": dash_store.MAX_LIST_SWAPS, "max_rows": dash_store.MAX_LIST_ROWS, "part_kinds": list(dash_store.LIST_PART_KINDS), "picks": list(dash_store.LIST_PICKS), "bases": list(dash_store.LIST_BASES), "sides": list(dash_store.LIST_SIDES), "reports": list(dash_store.LIST_REPORTS), "default": dash_store.check_list({}, [])}  # (B3) 목록 카드 설정 칸의 상한·선택지·처음 값
    found["dash"]["row_marks"] = {"kinds": list(dash_store.ROW_MARK_KINDS), "edits": list(dash_store.ROW_MARK_EDITS), "clashes": list(dash_store.ROW_MARK_CLASHES), "looks": list(dash_store.ROW_MARK_LOOKS), "max_fields": dash_store.MAX_ROW_MARK_FIELDS, "max_choices": dash_store.MAX_ROW_MARK_CHOICES, "max_name": dash_store.MAX_ROW_MARK_NAME, "max_text": dash_store.MAX_ROW_MARK_TEXT, "max_rows": dash_store.MAX_ROW_MARK_ROWS, "max_scan": dash_store.MAX_ROW_MARK_SCAN, "default": dash_store.ROW_MARKS_DEFAULT}  # (B9) 줄 표시 칸 설정의 선택지·상한·처음 값
    found["dash"]["digits"] = {"min": compare.MIN_DIGITS, "max": compare.MAX_DIGITS}  # (B6) 소수 자릿수 칸의 범위
    found["dash"].update(gone_kinds=list(dash_store.GONE_KINDS), gone_text=dash_store.GONE_CARD)  # 앱에서 없앤 카드 종류와 그 안내 글(화면이 서버와 같은 글자로 보인다)
    cal = settings.load()
    found.update(week_start=cal.get("week_start") or "mon", fiscal_start_month=cal.get("fiscal_start_month") or 1)  # 화면의 주·연 묶음(web/lib/calc.ts BUCKET)이 설정을 따르게
    found.update(currency_symbol=settings.get("currency_symbol"), currency_max=settings.CURRENCY_MAX)  # 값 모양 currency의 기호(web/lib/calc.ts showValue)와 설정 창 칸의 글자 수


PRELOAD = ("dashboard.dash_calc", "dashboard.dash_refresh", "dashboard.period", "dashboard.dash_pdf", "dashboard.summary_text")  # 대시보드를 열면 뒤에서 미리 올리는 기능 모듈


NEXT_PAGE = server.NEXT_DIR / "dashboard.html"  # web/을 빌드한 결과. 시험은 임시 파일로 바꿔 끼운다
NOT_BUILT = ("<!doctype html><meta charset=\"utf-8\"><title>Data Loupe</title>"
             "<p>대시보드 화면이 빌드되지 않았어요. web/에서 <code>npm ci</code> 뒤 <code>npm run build</code>를 실행하세요.</p>")  # NEXT_PAGE가 없을 때 /dashboard가 돌려주는 안내


@app.get("/dashboard")
def dashboard_page():
    preload(*PRELOAD)
    """대시보드 화면(web/out/dashboard.html, Next). 저장한 대시보드를 보고 고치는 한 곳이다. 연결 없이도 저장된 값으로 보인다."""
    if not NEXT_PAGE.is_file():  # 소스에서 web/을 빌드하지 않고 돌렸을 때. 옛 화면은 없다
        return Response(NOT_BUILT, mimetype="text/html")
    return server.next_page(NEXT_PAGE)


BUILD_PAGE = server.NEXT_DIR / "dashboard" / "build.html"  # 만드는 곳(web/app/dashboard/build). 시험은 임시 파일로 바꿔 끼운다


@app.get("/dashboard/build")
def dashboard_build_page():
    """대시보드 만드는 곳(web/out/dashboard/build.html, Next): 카드를 넣고 옮기고 고친다. /dashboard는 보기만 한다. 빌드가 없으면 NOT_BUILT."""
    preload(*PRELOAD)
    if not BUILD_PAGE.is_file():
        return Response(NOT_BUILT, mimetype="text/html")
    return server.next_page(BUILD_PAGE)


CHART_PAGE = server.NEXT_DIR / "chart.html"  # 차트 섬(iframe). 시험은 임시 파일로 바꿔 끼운다


@app.get("/chart")
def chart_page():
    """차트 섬(web/out/chart.html, Next): 쿼리·분석 화면이 iframe으로 끼우고 postMessage(web/lib/island.ts)로 그릴 것을 보낸다. 상수는 /api/dash/constants로 받는다."""
    if not CHART_PAGE.is_file():
        return Response(NOT_BUILT.replace("대시보드 화면이", "차트 화면이"), mimetype="text/html")
    response = server.next_page(CHART_PAGE)
    response.headers["Content-Security-Policy"] += "; frame-ancestors 'self'"  # 같은 출처의 화면만 끼울 수 있다
    return response


@app.get("/api/dash/constants")
def dash_constants_api():
    """대시보드 화면(Next)이 받는 상수(page가 옛 화면의 SERVER에 넣는 것과 같다). 연결을 쓰지 않는다(FREE)."""
    return jsonify(server.constants("dashboard"))


@app.get("/api/dash")
def dash_list():
    """목록 {dashboards: [{id, name, cards, updated}], locked, last(첫 화면에서 마지막으로 연 id)}. locked면 이 PC에서 풀 수 없는 파일이다."""
    return jsonify(**dash_store.listing(), last=(settings.read(LAST) or {}).get("id", ""))


@app.post("/api/dash/open")
def dash_open() -> Response:
    """[SPECIFICATION] plans/phase-10.md#dash_open
    대시보드를 열 때 예전 조인 이름을 새 이름으로 한 번 바꿔 저장하고 돌려준다(차례를 기다린다)."""
    d = dash_store.get_dashboard((request.json or {}).get("id"))
    return jsonify(dashboard=server.run(lambda: dash_calc.upgrade_dashboard(d)))


@app.post("/api/dash")
def dash_save():
    """대시보드 하나를 저장한다(id가 없으면 새 대시보드). 저장한 모양(카드 값 data는 뺀 것)과 새 목록을 돌려준다."""
    dash_refresh.check_pull_every(request.json)
    saved = dash_store.save_dashboard(request.json)
    slim = {**saved, "cards": [{k: v for k, v in c.items() if k != "data"} for c in saved["cards"]]}  # 화면은 보낸 카드를 이미 갖고 있다: 카드 값(data, 수 MB)은 돌려주지 않는다
    return jsonify(dashboard=slim, **dash_store.listing())


@app.post("/api/dash/delete")
def dash_delete():
    dash_id = request.json.get("id")
    dash_store.delete_dashboard(dash_id)
    with dash_files.LOCK:
        if dash_pull.OPEN["dash"] == dash_id:
            dash_pull.close_pc()
        dash_files.forget(dash_id)
        dash_keys.forget(dash_id)
        dash_marks.forget(dash_id)
    return jsonify(dash_store.listing())


@app.get("/api/dash/pull/state")
def pull_state() -> Response:
    """[SPECIFICATION] plans/dash-blocks.md#pull_state
    `GET /api/dash/pull/state?id=` 그 대시보드가 쓰는 받은 데이터: 크기 {bytes, text}와 칸마다 {slot, schema, table, bytes, pulled_at, shared, lost}(연결을 쓰지 않는다, FREE)."""
    dash_id = request.args.get("id", "")
    dash_store.get_dashboard(dash_id)
    found = dash_files.slots(dash_id)
    slots = []
    for slot, man in found.items():
        folder = dash_files.root() / slot
        size = sum((folder / name).stat().st_size for name in man["parts"] if (folder / name).is_file())
        slots.append({"slot": slot, "schema": man["schema"], "table": man["table"], "bytes": size,
                      "pulled_at": man["pulled_at"], "shared": len(man["users"]) > 1, "lost": man["lost"]})
    n = dash_files.size_of(dash_id)
    return jsonify(bytes=n, text=scan_size.size_text(n), slots=slots)


@app.post("/api/dash/pull/tables")
def pull_tables() -> Response:
    """[SPECIFICATION] plans/dash-blocks.md#pull_tables
    `POST /api/dash/pull/tables` {id}: 지금 연결이 원격 DB인지와 그 대시보드 카드들이 읽는 테이블·새 줄 컬럼 후보 {remote, tables}."""
    body = request.json or {}
    d = dash_store.get_dashboard(body.get("id"))
    con = server.get_con()
    return jsonify(remote=held.remote(con), tables=server.run(lambda: dash_pull.mark_tables(d)))


@app.post("/api/dash/pull/crypt")
def pull_crypt() -> Response:
    """[SPECIFICATION] plans/dash-blocks.md#pull_crypt
    `POST /api/dash/pull/crypt` {id, crypt, pass, how}: 그 대시보드의 암호화(와 암호문)를 바꾸고, how가 rewrite면 받아 둔 파일을 새 열쇠로 다시 쓴다(암호문은 돌려주지 않는다, FREE)."""
    body = request.json or {}
    d = dash_store.get_dashboard(body.get("id"))
    dash_id, crypt, how, text = d["id"], body.get("crypt"), body.get("how"), body.get("pass")
    if how not in PULL_HOWS:
        raise ValueError("암호화를 바꾸는 방법(how)은 pull(다시 받기)·rewrite(풀어서 다시 쓰기)예요.")
    if text is not None and not isinstance(text, str):
        raise ValueError("암호문은 글자여야 해요.")
    if crypt == "pass" and not text and not dash_keys.load()["pass"].get(dash_id):
        raise ValueError(dash_keys.NO_PASS)
    old_keys = {}
    if how == "rewrite":  # 옛 암호문 열쇠는 set_pass가 덮기 전에 구한다
        old_keys = {s: dash_keys.slot_key(m["crypt"]["group"], dash_id, m["crypt"]["salt"]) for s, m in dash_files.slots(dash_id).items()}
    old_marks = dash_marks.key_of(dash_id)  # (B9) 옛 암호문 열쇠는 set_pass가 덮기 전에 구한다
    if text:
        dash_keys.set_pass(dash_id, text)
    saved = dash_store.update_settings(dash_id, {"pull": {**dash_store.check_pull(d.get("pull")), "crypt": crypt}})
    if how == "rewrite":
        with dash_files.LOCK:
            dash_pull.rewrite_crypt(saved, old_keys)
    dash_marks.recrypt(saved, old_marks)  # (B9) 표시는 다시 받을 수 없어 어느 방법이든 새 열쇠로 다시 쓴다
    return jsonify(dashboard=saved)


@app.post("/api/dash/pull/full")
def pull_full() -> Response:
    """[SPECIFICATION] plans/dash-blocks.md#pull_full
    `POST /api/dash/pull/full` {id, ctx}: [전체 갱신] 그 대시보드가 쓰는 칸을 모두 DB에서 전부 다시 받는다 {slots}."""
    body = request.json or {}
    d = dash_store.get_dashboard(body.get("id"))
    server.get_con()
    slots = server.run(lambda: dash_pull.full(d, body.get("ctx")))
    return jsonify(slots=slots)


@app.post("/api/dash/pull/range")
def pull_range() -> Response:
    """[SPECIFICATION] plans/dash-blocks.md#pull_range
    `POST /api/dash/pull/range` {id, slot, start, end}: 마지막 줄을 못 찾은 칸의 그 기간을 다시 받는다 {rows}."""
    body = request.json or {}
    d = dash_store.get_dashboard(body.get("id"))
    server.get_con()
    slot = dash_store.text(body.get("slot"), "칸", MAX_SLOT_ID, 1)
    start = dash_store.text(body.get("start"), "시작 날짜", DATE_CHARS, 1)
    end = dash_store.text(body.get("end"), "끝 날짜", DATE_CHARS, 1)
    return jsonify(**server.run(lambda: dash_pull.refetch_range(d, slot, start, end)))


BELL_FILE = "dash_bell.json"  # (B11) 설정 폴더의 알림 읽음 기록 {"read": [알림 id]}
MAX_BELL = 500  # (B11) 기억하는 읽은 알림 id 수
MAX_BELL_ID = 300  # (B11) 알림 id 글자 수


@app.get("/api/dash/bell")
def bell_get() -> Response:
    """[SPECIFICATION] plans/dash-blocks.md#bell_get
    `GET /api/dash/bell` 읽은 알림 id 목록 {read}(FREE)."""
    found = settings.read(BELL_FILE) or {}
    read = found.get("read")
    if not isinstance(read, list) or not all(isinstance(x, str) for x in read):
        read = []
    return jsonify(read=read)


@app.post("/api/dash/bell")
def bell_set() -> Response:
    """[SPECIFICATION] plans/dash-blocks.md#bell_set
    `POST /api/dash/bell` {read, current}: 읽은 알림 id를 더하고, 지금 있는 알림(current)에 없는 옛 기록은 지워 {read}를 돌려준다(FREE)."""
    body = request.json or {}
    read, current = body.get("read"), body.get("current")
    if not isinstance(read, list) or not isinstance(current, list):
        raise ValueError("알림 id 목록이 맞지 않아요.")
    new = [dash_store.text(x, "알림 id", MAX_BELL_ID, 1) for x in read]
    now = {dash_store.text(x, "알림 id", MAX_BELL_ID, 1) for x in current}
    with settings.updating(BELL_FILE):
        old = (settings.read(BELL_FILE) or {}).get("read")
        if not isinstance(old, list):
            old = []
        keep = list(dict.fromkeys(x for x in [*old, *new] if isinstance(x, str) and x in now))[-MAX_BELL:]
        settings.write(BELL_FILE, {"read": keep})
    return jsonify(read=keep)


@app.get("/api/dash/pull/orphans")
def pull_orphans() -> Response:
    """[SPECIFICATION] plans/dash-blocks.md#pull_orphans
    `GET /api/dash/pull/orphans` 아무 대시보드도 쓰지 않는 받은 데이터 {orphans: [{slot, schema, table, bytes}], text}(FREE)."""
    store = dash_store.load()
    if store["locked"]:
        return jsonify(orphans=[], text="0 B")
    found = dash_files.orphans(set(store["dashboards"]))
    return jsonify(orphans=found, text=scan_size.size_text(sum(o["bytes"] for o in found)))


@app.post("/api/dash/pull/orphans/drop")
def pull_orphans_drop() -> Response:
    """[SPECIFICATION] plans/dash-blocks.md#pull_orphans_drop
    `POST /api/dash/pull/orphans/drop` 사용자가 확인한 뒤 주인 없는 받은 데이터를 지운다 {dropped}(FREE)."""
    store = dash_store.load()
    if store["locked"]:
        raise ValueError(dash_store.LOCKED)
    known = set(store["dashboards"])
    with dash_files.LOCK:
        orphan = {o["slot"] for o in dash_files.orphans(known)}
        if any(p.parent.name in orphan for paths in dash_pull.OPEN["views"].values() for p in paths):
            dash_pull.close_pc()
        dropped = dash_files.drop_orphans(known)
    return jsonify(dropped=dropped)


@app.post("/api/dash/pull/unused")
def pull_unused() -> Response:
    """[SPECIFICATION] plans/dash-blocks.md#pull_unused
    `POST /api/dash/pull/unused` {id}: 그 대시보드에서 더는 쓰지 않는 받은 파일 {unused: [{slot, schema, table, bytes, shared}]}."""
    body = request.json or {}
    d = dash_store.get_dashboard(body.get("id"))
    server.get_con()
    return jsonify(unused=server.run(lambda: dash_pull.unused(d)))


@app.post("/api/dash/pull/unused/drop")
def pull_unused_drop() -> Response:
    """[SPECIFICATION] plans/dash-blocks.md#pull_unused_drop
    `POST /api/dash/pull/unused/drop` {id, slots}: 사용자가 확인한 칸 가운데 지금도 안 쓰는 것만 지운다 {dropped, kept}."""
    body = request.json or {}
    slots = body.get("slots")
    if not isinstance(slots, list) or not all(isinstance(s, str) for s in slots):
        raise ValueError("지울 칸(slots)은 글자 목록이에요.")
    d = dash_store.get_dashboard(body.get("id"))
    server.get_con()
    still = {u["slot"] for u in server.run(lambda: dash_pull.unused(d))}
    for slot in slots:
        if slot in still:
            dash_files.drop_user(slot, d["id"])
    return jsonify(dropped=[s for s in slots if s in still], kept=[s for s in slots if s not in still])


@app.post("/api/dash/marks/set")
def marks_set() -> Response:
    """[SPECIFICATION] plans/dash-blocks.md#marks_set
    `POST /api/dash/marks/set` {dash, card, key, values}: 목록 줄 하나의 표시를 바꾼다 {key, values}(설정 폴더 파일만, FREE)."""
    body = request.json or {}
    d = dash_store.get_dashboard(body.get("dash"))
    card = dash_marks.list_card(d, body.get("card"))
    return jsonify(**dash_marks.set_mark(d, card, body.get("key"), body.get("values")))


@app.post("/api/dash/marks/save")
def marks_save() -> Response:
    """[SPECIFICATION] plans/dash-blocks.md#marks_save
    `POST /api/dash/marks/save` {dash, card}: 저장 대화상자로 표시를 파일로 저장한다 {path}(FREE)."""
    body = request.json or {}
    d = dash_store.get_dashboard(body.get("dash"))
    card = dash_marks.list_card(d, body.get("card"))
    return jsonify(path=dash_marks.save_file(d, card))


@app.post("/api/dash/marks/load")
def marks_load() -> Response:
    """[SPECIFICATION] plans/dash-blocks.md#marks_load
    `POST /api/dash/marks/load` {dash, card}: 열기 대화상자로 고른 파일의 표시를 가져온다 {path, added, clashed, clash}(FREE)."""
    body = request.json or {}
    d = dash_store.get_dashboard(body.get("dash"))
    card = dash_marks.list_card(d, body.get("card"))
    got = dash_marks.load_file(d, card)
    return jsonify(**got) if got else jsonify(path=None)


def mark_present(d: dict, card: dict) -> list[str]:
    """[SPECIFICATION] plans/dash-blocks.md#mark_present
    목록 카드의 지금 데이터 열쇠들: PC 계산이면 받아 둔 파일(DB에 묻지 않음), 아니면 지금 연결에서 dash_calc.list_keys."""
    if dash_pull.uses_pc(d["id"]):
        files, _ = dash_pull.held_files(d, None)
        with dash_pull.on_pc(files, d["id"]):
            return dash_calc.list_keys(card)
    server.get_con()
    if other_source(card["source"].get("key"), dash_store.current_source()):
        raise ValueError("다른 연결에서 만든 목록이라 지금 데이터와 견줄 수 없어요. 그 연결로 바꾼 뒤 다시 세어 주세요.")
    return dash_calc.list_keys(card)


@app.post("/api/dash/marks/orphans")
def marks_orphans() -> Response:
    """[SPECIFICATION] plans/dash-blocks.md#marks_orphans
    `POST /api/dash/marks/orphans` {dash, card}: 지금 데이터에 없는 줄의 표시 수 {count}(차례를 기다린다)."""
    body = request.json or {}
    d = dash_store.get_dashboard(body.get("dash"))
    card = dash_marks.list_card(d, body.get("card"))
    return jsonify(count=len(dash_marks.orphans(d["id"], card["id"], mark_present(d, card))))


@app.post("/api/dash/marks/orphans/drop")
def marks_orphans_drop() -> Response:
    """[SPECIFICATION] plans/dash-blocks.md#marks_orphans_drop
    `POST /api/dash/marks/orphans/drop` {dash, card}: 다시 세어 지금 데이터에 없는 줄의 표시를 지운다 {dropped}(차례를 기다린다)."""
    body = request.json or {}
    d = dash_store.get_dashboard(body.get("dash"))
    card = dash_marks.list_card(d, body.get("card"))
    gone = dash_marks.orphans(d["id"], card["id"], mark_present(d, card))
    return jsonify(dropped=dash_marks.drop_keys(d, card["id"], gone))


@app.post("/api/dash/card/add")
def card_add():
    """카드 하나를 저장한 대시보드(dashboard: id)에 더한다. id가 비면 name으로 새 대시보드를 만든다. 카드의 연결(key·label)은 지금 연결로 채운다."""
    body = request.json
    card = dash_store.check_card(with_source(body.get("card") or {}))
    saved = dash_store.add_card(body.get("dashboard") or "", body.get("name"), card)
    return jsonify(dashboard={"id": saved["id"], "name": saved["name"], "cards": len(saved["cards"])}, **dash_store.listing())


def other_source(key, now):
    """key가 있는데 지금 연결(now)과 다른 연결의 것인지."""
    return bool(now and key and not use_counts.same_source(key, now["key"]))


@app.post("/api/dash/card/compute")
def card_compute():
    """카드 하나의 source로 data를 구해 돌려준다(저장하지 않는다). source.key가 있는데 지금 연결과 다르면 거부한다. 지금 연결의 key·label도 함께 준다."""
    body = request.json or {}
    card = dash_store.check_card(body.get("card") or {})
    dash_id = dash_store.text(body.get("dash"), "대시보드", MAX_DASH_ID)
    fresh = dash_store.text(body.get("fresh"), "갱신 표", MAX_FRESH_ID)
    pc = dash_pull.uses_pc(dash_id)
    now = dash_store.current_source()
    if other_source(card["source"].get("key"), now):
        raise ValueError(f"이 카드는 다른 연결({card['source'].get('label') or card['source']['key']})에서 구한 거예요. 그 연결로 바꾼 뒤 다시 구하세요.")
    if not pc:
        found = dash_refresh.scan_found(card, body.get("ctx"))  # 최신화와 같은 처리 용량 판정. 넘으면 화면이 묻고(confirmed) 다시 보낸다
        if found.get("blocked") or (found.get("over") and body.get("confirmed") is not True):
            response = jsonify(needs_confirm=True, **found)
            response.headers["X-Needs-Confirm"] = "1"
            return response
    return jsonify(data=dash_pull.compute(card, dt.datetime.now().isoformat(timespec="seconds"), body.get("ctx"), dash_id, fresh), source=now)


@app.post("/api/dash/card/hours")
def card_hours() -> Response:
    """(B10b) KPI 큰 창의 시간 눈금 값 {labels, values, reason}(kpi_hours.hour_values). 다른 연결의 카드·처리 용량을 넘는 카드는 거부하고, 보는 사람의 필터(ctx.filters)는 건다."""
    body = request.json or {}
    card = dash_store.check_card(body.get("card") or {})
    if other_source(card["source"].get("key"), dash_store.current_source()):
        raise ValueError("다른 연결에서 만든 카드라 지금은 시간 눈금을 볼 수 없어요.")
    dash_id = dash_store.text(body.get("dash"), "대시보드", MAX_DASH_ID)
    if not dash_pull.uses_pc(dash_id):
        if why := dash_refresh.scan_decision(card, body.get("ctx")):
            raise ValueError(why)
        return jsonify(kpi_hours.hour_values(dash_calc.with_filters(card, body.get("ctx"))[0], body.get("ctx")))
    files, _ = dash_pull.files_for(card, body.get("ctx"), dash_id, "")
    with dash_pull.on_pc(files, dash_id):
        return jsonify(kpi_hours.hour_values(dash_calc.with_filters(card, body.get("ctx"))[0], body.get("ctx")))


@app.post("/api/dash/filter/values")
def filter_values():
    """필터 칸에 보일 값 목록 {values, more}: 저장한 대시보드(id)의 지금 연결 카드들에서 column의 서로 다른 값(dash_calc.filter_values). filters(칩 줄이 받는 링크 필터)가 있으면 그것으로 좁힌다."""
    body, now = request.json or {}, dash_store.current_source()
    pc = dash_pull.uses_pc(body.get("id"))
    if not now and not pc:
        raise ValueError("데이터에 연결하면 거를 수 있어요.")
    d = dash_store.get_dashboard(body.get("id"))
    cards = [c for c in d["cards"] if not now or not other_source(c["source"].get("key"), now)]
    column = dash_store.text(body.get("column"), "필터 컬럼", dash_store.MAX_FILTER_NAME, 1)
    filters = body.get("filters") or []
    if not pc:
        values, more, missed = dash_calc.filter_values(cards, column, filters)
        return jsonify(values=values, more=more, note=dash_calc.filter_note(missed))
    files, _ = dash_pull.held_files(d, None)
    with dash_pull.on_pc(files, d["id"]):
        values, more, missed = dash_calc.filter_values(cards, column, filters)
    return jsonify(values=values, more=more, note=dash_calc.filter_note(missed))


@app.get("/api/viz")
def viz_prefs():
    """[SPECIFICATION] plans/phase-9.md#viz_prefs
    시각화 설정(낱말 목록·쿼리 화면 자동 규칙 기본값) viz_rules.load_prefs()를 돌려준다."""
    return jsonify(viz_rules.load_prefs())


@app.post("/api/viz")
def save_viz_prefs():
    """[SPECIFICATION] plans/phase-9.md#save_viz_prefs
    body(시각화 설정 전체)를 viz_rules.save_prefs로 저장하고 저장한 것을 돌려준다(틀리면 server.on_error의 400 문장)."""
    return jsonify(viz_rules.save_prefs(request.json))


@app.post("/api/chart/points")
def chart_points():
    """산점도·버블의 점: body = 차트 조회(테이블·조건 …, x, series[0].y 또는 y, split)와 size(버블의 크기 컬럼). 점·전체 수·뽑았는지·r·추세선을 돌려준다.
    source_key가 있는데 지금 연결과 다르면 거부한다."""
    body = request.json
    now = dash_store.current_source()
    if other_source(body.get("source_key"), now):
        raise ValueError("이 카드는 다른 연결에서 구한 거예요. 그 연결로 바꾼 뒤 다시 구하세요.")
    body = dash_calc.with_period(body, body.get("date"), (body.get("ctx") or {}).get("range"))  # 대시보드 기간(카드 compute와 같게)
    body = dash_calc.with_filters({"source": {"body": body}}, body.get("ctx"))[0]["source"]["body"]  # 보는 사람의 필터(걸 수 있는 것만)
    dash_id = dash_store.text(body.get("dash"), "대시보드", MAX_DASH_ID)
    if not dash_pull.uses_pc(dash_id):
        try:
            querying.runnable(body, select=False, ask=body)  # 처리 용량 확인만(BigQuery의 읽는 양, 원격에서 새로 받을 사본). 받은 사본은 points_of가 그대로 쓴다
        except querying.Confirm as e:
            return e.response
        return jsonify(points=dash_calc.points_of(body, body.get("size"), body.get("auto")))
    sized = [{"y": body["size"], "agg": "count"}] if body.get("size") else []  # 크기 컬럼도 받게
    card = {"id": "", "kind": "chart", "view": {}, "source": {"body": {**body, "series": [*(body.get("series") or []), *sized]}, "date": body.get("date")}}
    files, _ = dash_pull.files_for(card, body.get("ctx"), dash_id, "")
    with dash_pull.on_pc(files, dash_id):
        return jsonify(points=dash_calc.points_of(body, body.get("size"), body.get("auto")))


@app.post("/api/dash/cards/compute")
def cards_compute():
    """카드 여러 개({cards: [카드]})의 값을 지금 연결에서 차례로 구한다(저장하지 않는다). 카드마다 {id, data} 또는 {id, error}. 실시간 갱신이 쓴다.
    구한 데 걸린 초(seconds)도 준다. 다른 연결의 카드는 error로 돌려준다."""
    now, started, out = dash_store.current_source(), time.monotonic(), []
    stamp = dt.datetime.now().isoformat(timespec="seconds")
    body = request.json or {}
    given = body.get("cards") or []
    dash_id = dash_store.text(body.get("dash"), "대시보드", MAX_DASH_ID)
    fresh = dash_store.text(body.get("fresh"), "갱신 표", MAX_FRESH_ID)
    pc = dash_pull.uses_pc(dash_id)
    ctx = dash_calc.shared_context([c for c in given if isinstance(c, dict)], body.get("ctx"))  # 같은 테이블·조건의 카드들은 한 번에 읽는다(최신화와 같은 계획)
    for one in given:
        try:
            card = dash_store.check_card(one)
            if other_source(card["source"].get("key"), now):
                raise ValueError("다른 연결에서 구한 카드예요")
            if not pc and (why := dash_refresh.scan_decision(card, ctx)):  # 실시간 갱신은 묻지 않고 그 카드만 건너뛴다(최신화와 같게)
                raise ValueError(why)
            out.append({"id": card["id"], "data": dash_pull.compute(card, stamp, ctx, dash_id, fresh)})
        except ValueError as e:
            out.append({"id": (one or {}).get("id", ""), "error": str(e)})
    return jsonify(results=out, seconds=round(time.monotonic() - started, 2))


@app.post("/api/dash/settings")
def dash_settings():
    """저장한 대시보드의 기준일(as_of)·기간(period)·비교 기준(compare_mode)·최신화(refresh)·실시간(live)만 바꾼다(카드는 그대로). 최신화·실시간 주기를 검사한다."""
    body = request.json
    saved = dash_store.get_dashboard(body.get("id"))
    fields = {k: body[k] for k in dash_store.SETTING_KEYS if k in body}
    dash_refresh.check_pull_every({**saved, **fields})
    return jsonify(dashboard=dash_store.update_settings(body.get("id"), fields))


@app.post("/api/dash/last")
def dash_last():
    """첫 화면에서 마지막으로 연 대시보드 id를 기억한다(빈 글자면 지운다)."""
    dash_id = (request.json or {}).get("id") or ""
    if dash_id:
        dash_store.get_dashboard(dash_id)
    settings.write(LAST, {"id": dash_id})
    return jsonify(ok=True)


@app.get("/api/dash/live_min")
def live_min_get():
    """지금 연결과 계산 방식 DB의 갱신 주기 최소(초)"""
    now = dash_store.current_source()
    return jsonify(source=now, seconds=dash_refresh.live_min(now["key"]) if now else 0)


@app.post("/api/period/parse")
def period_parse():
    """기간 글자(text)를 기준일(as_of, 비면 오늘) 기준으로 읽는다. 비교 기준(compare_mode, 비면 prev)으로 견줄 기간을 정한다. {span: {start, end, days, label, error, ...}, previous: 비교 기간 또는 None, context: 카드를 구할 때 쓰는 {range, prev}(기준일로 자른 것, 비었거나 읽을 수 없으면 None)}.
    규칙은 period.py와 dash_refresh.context 한 곳에 있다(화면은 이 응답만 쓴다)."""
    body = request.json or {}
    day = (body.get("as_of") or "").strip() or None
    span = period.parse(body.get("text"), day)
    if not span["error"] and body.get("dashboard") and dash_refresh.timed(span):  # 문장·지표 사전의 기간 칸은 시각도 쓴다
        span = {**span, "error": "대시보드 기간은 날짜로 써 주세요(시각은 쓰지 않아요)."}
    mode = body.get("compare_mode") or "prev"
    return jsonify(span=span, previous=None if span["error"] else period.previous(span, mode), context=dash_refresh.context({"period": body.get("text"), "as_of": day, "compare_mode": mode}))


@app.post("/api/period/day")
def period_day():
    """기준일 글자(text)를 읽는다: {date, label("2026-10-01(목) 기준"), error}. 오늘보다 뒤의 날은 막는다."""
    return jsonify(period.parse_day((request.json or {}).get("text")))


@app.post("/api/compare")
def compare_sentences():
    """비교 문장 여러 개({items: [{cur, prev, fmt, mode, label, better, partial, digits, zeros}]})를 compare.compare로 만든다."""
    items = (request.json or {}).get("items")
    if not isinstance(items, list) or len(items) > dash_store.MAX_CARDS:
        raise ValueError(f"비교할 값은 {dash_store.MAX_CARDS}개까지 보낼 수 있어요.")
    keys = ("cur", "prev", "fmt", "mode", "label", "better", "partial", "digits", "zeros")
    return jsonify(results=[compare.compare(**{k: i[k] for k in keys if k in i}) for i in items])


@app.get("/api/targets")
def targets_get():
    """목표 파일 경로(설정 targets_file.json)와 그 파일에서 읽은 목표 줄, 문제 줄 한 쪽(쿼리 offset부터 limit개, limit은 TARGET_PAGE까지)과 전체 문제 줄 수 error_total.
    경로가 없으면 빈 값, 읽을 수 없으면 error."""
    offset = max(0, request.args.get("offset", 0, type=int))
    limit = min(max(0, request.args.get("limit", TARGET_PAGE, type=int)), TARGET_PAGE)
    path = (settings.read(dash_calc.TARGETS_FILE) or {}).get("path", "")
    if not path:
        return jsonify(path="", rows=[], errors=[], error_total=0, offset=offset, limit=limit, error="")
    try:
        found = targets.read(path, offset, limit)
    except ValueError as e:
        return jsonify(path=path, rows=[], errors=[], error_total=0, offset=offset, limit=limit, error=str(e))
    return jsonify(path=path, rows=found["rows"], errors=found["errors"], error_total=found["error_total"], offset=offset, limit=limit, error="")


@app.post("/api/targets")
def targets_set():
    """목표 파일 경로를 저장한다(경로만. 읽어 보고 읽을 수 있을 때만 저장, 비우면 지움). 읽은 결과를 돌려준다."""
    path = ((request.json or {}).get("path") or "").strip()
    if path:
        targets.read(path, 0, 0)
    settings.write(dash_calc.TARGETS_FILE, {"path": path})
    return targets_get()


@app.get("/api/dash/image")
def dash_image():
    """배치도 카드(map, 바탕 image)에 저장된 그림 파일 경로의 png·jpg·svg를 8MB까지 내려준다. 경로는 저장된 카드의 것만 쓰고(요청이 정하지 않는다) 그 밖의 파일은 주지 않는다.
    ?dash=대시보드 id&card=카드 id."""
    d = dash_store.get_dashboard(request.args.get("dash"))
    card = next((c for c in d["cards"] if c["id"] == request.args.get("card")), None)
    if card is None or card["kind"] != "map" or card["view"].get("bg") != "image" or not card["view"].get("image_path"):
        raise ValueError("그림 바탕이 정해진 배치도 카드가 아니에요. 카드를 저장한 뒤 다시 여세요.")
    path = Path(card["view"]["image_path"])
    if path.suffix.lower() not in IMAGE_TYPES:
        raise ValueError("그림은 png·jpg·svg 파일만 쓸 수 있어요.")
    if not path.is_file():
        raise ValueError("그림 파일을 찾을 수 없어요. 경로를 확인해 주세요.")
    if path.stat().st_size > MAX_IMAGE_BYTES:
        raise ValueError(f"그림은 {MAX_IMAGE_BYTES // 1024 // 1024}MB까지 쓸 수 있어요.")
    return Response(path.read_bytes(), mimetype=IMAGE_TYPES[path.suffix.lower()], headers={"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox", "Cache-Control": "no-store"})


# ---------- 내 템플릿(kind·title·layout·colors만 저장) ----------

@app.get("/api/dash/templates")
def templates_list():
    return jsonify(templates=dash_store.load_templates())


@app.post("/api/dash/templates")
def templates_save():
    """내 템플릿 저장: {name, cards: [{kind, title, layout, colors}]}. 같은 이름이면 바꾼다."""
    return jsonify(templates=dash_store.save_template(request.json or {}))


@app.post("/api/dash/templates/delete")
def templates_delete():
    return jsonify(templates=dash_store.delete_template((request.json or {}).get("id")))


# ---------- 보고서 PDF ----------

def pdf_stem(name):
    """PDF 파일 이름(확장자 앞): 파일 이름에 못 쓰는 글자를 바꾸고 비면 '대시보드'."""
    stem = server.BAD_NAME_CHARS.sub("_", str(name or "")).strip(" .")[:80]
    return stem if stem and stem.upper() not in server.RESERVED else "대시보드"


@app.post("/api/dash/pdf")
def dash_pdf_save():
    """보고서 그림(JPEG data URL) 하나를 1쪽 PDF로 만들어 창의 저장 대화상자로 고른 곳에 쓴다(dash_pdf.py). 창 없이 돌 때는 PDF 자체(data URL)를 돌려줘 화면이 내려받게 한다."""
    body = request.json or {}
    pdf = dash_pdf.make_pdf(dash_pdf.decode_image(body.get("image")))
    name = pdf_stem(body.get("name")) + ".pdf"
    if server.ask_save is None:
        import base64
        return jsonify(window=False, name=name, pdf="data:application/pdf;base64," + base64.b64encode(pdf).decode("ascii"))
    chosen = server.ask_save(name, ("PDF (*.pdf)",))
    if not chosen:
        return jsonify(cancelled=True)
    path = Path(chosen if chosen.lower().endswith(".pdf") else chosen + ".pdf")
    part = path.with_name(path.name + ".part")
    try:
        part.write_bytes(pdf)
        part.replace(path)  # 임시 이름으로 쓴 뒤 바꿔치기(실패해도 원래 파일이 남는다)
    except BaseException:
        part.unlink(missing_ok=True)  # 반쯤 쓴 임시 파일을 남기지 않는다
        raise
    return jsonify(path=str(path))
