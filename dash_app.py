"""대시보드의 라우트. server.app에 달린다. 저장·목록·지우기·카드 더하기·기간 읽기·비교 문장·최신화 상태는 연결을 쓰지 않으므로 차례(turn)를 기다리지 않는다.
카드 값을 구하는 /api/dash/card/compute와 /api/dash/cards/compute만 연결에서 구한다(차례를 기다린다). 최신화는 dash_refresh의 작업 스레드가 한다."""
import datetime as dt
import re
import time
from pathlib import Path

from flask import Response, jsonify, request

import compare
import dash_calc
import dash_refresh
import dash_pdf
import dash_store
import metrics
import period
import server
import settings
import targets
from dash_refresh import current_source
from server import app

server.CONSTANTS["dash"] = {"max_cards": dash_store.MAX_CARDS, "max_dashboards": dash_store.MAX_DASHBOARDS, "max_palette": dash_store.MAX_PALETTE,
                            "types": dash_store.CHART_TYPES, "time_types": dash_store.TIME_TYPES, "units": dash_store.UNITS,
                            "palette": dash_store.DEFAULT_PALETTE, "locked": dash_store.LOCKED, "min_refresh": dash_refresh.MIN_REFRESH}  # 화면(dash.js)이 서버와 같은 상한·종류·기본 색을 쓴다
server.CONSTANTS["dash"]["max_points"] = dash_calc.MAX_POINTS  # 산점도·버블의 점 수 상한
server.CONSTANTS["dash"].update(aggs=list(dash_calc.AGGS), merge=list(dash_calc.MERGE), agg_names=metrics.AGG_NAMES)  # 카드 계열의 집계(함수 사전의 집계 전부), 일 값을 다시 묶을 수 있는 집계, 보이는 이름
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".svg": "image/svg+xml"}  # 배치도 바탕 그림으로 내려줄 수 있는 것
MAX_IMAGE_BYTES = 8 * 1024 * 1024
LAST = "dash_last.json"  # 첫 화면에서 마지막으로 연 대시보드 {id}
server.FREE.update({"/api/dash", "/api/dash/get", "/api/dash/delete", "/api/dash/card/add", "/api/dash/settings", "/api/dash/last", "/api/dash/live_min", "/api/dash/refresh/start",
                    "/api/dash/refresh/status", "/api/dash/refresh/stop", "/api/period/parse", "/api/period/day", "/api/period/every", "/api/compare", "/api/targets", "/api/dash/image", "/dashboard", "/api/dash/pdf", "/api/dash/templates", "/api/dash/templates/delete"})


def with_source(card):
    """카드의 source에 지금 연결의 key·label을 넣은 사본(연결이 없으면 그대로)."""
    now = current_source()
    return {**card, "source": {**(card.get("source") or {}), **(now or {})}} if now else card


@app.get("/dashboard")
def dashboard_page():
    """대시보드 화면(dashboard.html). 저장한 대시보드를 보고 고치는 한 곳이다. 연결 없이도 저장된 값으로 보인다."""
    return server.page("dashboard.html")


@app.get("/api/dash")
def dash_list():
    """목록 {dashboards: [{id, name, cards, updated}], locked, last(첫 화면에서 마지막으로 연 id)}. locked면 이 PC에서 풀 수 없는 파일이다."""
    return jsonify(**dash_store.listing(), last=(settings.read(LAST) or {}).get("id", ""))


@app.get("/api/dash/get")
def dash_get():
    """대시보드 하나(카드·값 포함): /api/dash/get?id=…. 연결을 쓰지 않으므로 차례를 기다리지 않는다(경로 변수를 쓰면 FREE에 넣을 수 없다)."""
    return jsonify(dashboard=dash_store.get_dashboard(request.args.get("id")))


@app.post("/api/dash")
def dash_save():
    """대시보드 하나를 저장한다(id가 없으면 새 대시보드). 저장한 모양과 새 목록을 돌려준다."""
    dash_refresh.check_dashboard_every(request.json)
    saved = dash_store.save_dashboard(request.json)
    return jsonify(dashboard=saved, **dash_store.listing())


@app.post("/api/dash/delete")
def dash_delete():
    dash_store.delete_dashboard(request.json.get("id"))
    return jsonify(dash_store.listing())


@app.post("/api/dash/card/add")
def card_add():
    """카드 하나를 저장한 대시보드(dashboard: id)에 더한다. id가 비면 name으로 새 대시보드를 만든다. 카드의 연결(key·label)은 지금 연결로 채운다."""
    body = request.json
    card = dash_store.check_card(with_source(body.get("card") or {}))
    saved = dash_store.add_card(body.get("dashboard") or "", body.get("name"), card)
    return jsonify(dashboard={"id": saved["id"], "name": saved["name"], "cards": len(saved["cards"])}, **dash_store.listing())


@app.post("/api/dash/card/compute")
def card_compute():
    """카드 하나의 source로 data를 구해 돌려준다(저장하지 않는다). source.key가 있는데 지금 연결과 다르면 거부한다. 지금 연결의 key·label도 함께 준다."""
    card = dash_store.check_card((request.json or {}).get("card") or {})
    now = current_source()
    if now and card["source"].get("key") and card["source"]["key"] != now["key"]:
        raise ValueError(f"이 카드는 다른 연결({card['source'].get('label') or card['source']['key']})에서 구한 것입니다. 그 연결로 바꾼 뒤 다시 구하세요.")
    return jsonify(data=dash_calc.compute(card, dt.datetime.now().isoformat(timespec="seconds"), request.json.get("ctx")), source=now)


@app.post("/api/chart/points")
def chart_points():
    """산점도·버블의 점: body = 차트 조회(테이블·조건 …, x, series[0].y 또는 y, split)와 size(버블의 크기 컬럼). 점·전체 수·뽑았는지·r·추세선을 돌려준다.
    source_key가 있는데 지금 연결과 다르면 거부한다."""
    body = request.json
    now = current_source()
    if now and body.get("source_key") and body["source_key"] != now["key"]:
        raise ValueError("이 카드는 다른 연결에서 구한 것입니다. 그 연결로 바꾼 뒤 다시 구하세요.")
    return jsonify(points=dash_calc.points_of(body, body.get("size")))


@app.post("/api/dash/cards/compute")
def cards_compute():
    """카드 여러 개({cards: [카드]})의 값을 지금 연결에서 차례로 구한다(저장하지 않는다). 카드마다 {id, data} 또는 {id, error}. 실시간 갱신이 쓴다.
    구한 데 걸린 초(seconds)도 준다. 다른 연결의 카드는 error로 돌려준다."""
    now, started, out = current_source(), time.monotonic(), []
    stamp = dt.datetime.now().isoformat(timespec="seconds")
    given = (request.json or {}).get("cards") or []
    ctx = dash_calc.shared_context([c for c in given if isinstance(c, dict)], (request.json or {}).get("ctx"))  # 같은 테이블·조건의 카드들은 한 번에 읽는다(최신화와 같은 계획)
    for one in given:
        try:
            card = dash_store.check_card(one)
            if now and card["source"].get("key") and card["source"]["key"] != now["key"]:
                raise ValueError("다른 연결에서 구한 카드예요")
            out.append({"id": card["id"], "data": dash_calc.compute(card, stamp, ctx)})
        except ValueError as e:
            out.append({"id": (one or {}).get("id", ""), "error": str(e)})
    return jsonify(results=out, seconds=round(time.monotonic() - started, 2))


@app.post("/api/dash/settings")
def dash_settings():
    """저장한 대시보드의 기준일(as_of)·기간(period)·최신화(refresh)·실시간(live)만 바꾼다(카드는 그대로). 최신화·실시간 주기를 검사한다."""
    body = request.json
    saved = dash_store.get_dashboard(body.get("id"))
    fields = {k: body[k] for k in ("as_of", "period", "refresh", "live") if k in body}
    dash_refresh.check_dashboard_every({**saved, **fields})
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
    """지금 연결의 실시간 간격 최소(초, 0이면 제한 없음)와 연결 이름."""
    now = current_source()
    return jsonify(source=now, seconds=dash_refresh.live_min(now["key"]) if now else 0)


@app.post("/api/dash/live_min")
def live_min_set():
    """지금 연결의 실시간 간격 최소(초)를 사용자가 정한다. 0이면 지운다."""
    now = current_source()
    dash_refresh.set_live_min(now["key"] if now else None, (request.json or {}).get("seconds"))
    return live_min_get()


@app.post("/api/dash/refresh/start")
def refresh_start():
    """연결에 성공한 뒤 화면이 부른다: 주기가 지난 카드의 최신화를 작업 스레드에서 시작하고 상태를 돌려준다."""
    return jsonify(dash_refresh.start())


@app.get("/api/dash/refresh/status")
def refresh_status():
    return jsonify(dash_refresh.status())


@app.post("/api/dash/refresh/stop")
def refresh_stop():
    return jsonify(dash_refresh.stop())


@app.post("/api/period/parse")
def period_parse():
    """기간 글자(text)를 기준일(as_of, 비면 오늘) 기준으로 읽는다. {span: {start, end, days, label, error, ...}, previous: 직전 기간 또는 None, context: 카드를 구할 때 쓰는 {range, prev}(기준일로 자른 것, 비었거나 읽을 수 없으면 None)}.
    규칙은 period.py와 dash_refresh.context 한 곳에 있다(화면은 이 응답만 쓴다)."""
    body = request.json or {}
    day = (body.get("as_of") or "").strip() or None
    span = period.parse(body.get("text"), day)
    return jsonify(span=span, previous=None if span["error"] else period.previous(span), context=dash_refresh.context({"period": body.get("text"), "as_of": day}))


@app.post("/api/period/day")
def period_day():
    """기준일 글자(text)를 읽는다: {date, label("2026-10-01(목) 기준"), error}. 오늘보다 뒤의 날은 막는다."""
    return jsonify(period.parse_day((request.json or {}).get("text")))


@app.post("/api/period/every")
def period_every():
    """주기 글자(text)를 초로 읽는다. kind가 refresh면 최신화 주기(1시간 이상), live면 실시간 간격(지금 연결의 최소 이상). {seconds, error}."""
    body = request.json or {}
    now = current_source()
    try:
        if body.get("kind") == "live":
            seconds = dash_refresh.every_seconds(body.get("text"), max(1, dash_refresh.live_min(now["key"]) if now else 0), "실시간")
        else:
            seconds = dash_refresh.every_seconds(body.get("text"), dash_refresh.MIN_REFRESH, "최신화")
    except ValueError as e:
        return jsonify(seconds=None, error=str(e))
    return jsonify(seconds=seconds, error=None)


@app.post("/api/compare")
def compare_sentences():
    """비교 문장 여러 개({items: [{cur, prev, fmt, mode, label, better, partial}]})를 compare.compare로 만든다."""
    items = (request.json or {}).get("items")
    if not isinstance(items, list) or len(items) > dash_store.MAX_CARDS:
        raise ValueError(f"비교할 값은 {dash_store.MAX_CARDS}개까지 보낼 수 있습니다.")
    keys = ("cur", "prev", "fmt", "mode", "label", "better", "partial")
    return jsonify(results=[compare.compare(**{k: i[k] for k in keys if k in i}) for i in items])


@app.get("/api/targets")
def targets_get():
    """목표 파일 경로(설정 targets_file.json)와 그 파일에서 읽은 목표 줄·문제 줄. 경로가 없으면 빈 값, 읽을 수 없으면 error."""
    path = (settings.read(dash_calc.TARGETS_FILE) or {}).get("path", "")
    if not path:
        return jsonify(path="", rows=[], errors=[], error="")
    try:
        found = targets.read(path)
    except ValueError as e:
        return jsonify(path=path, rows=[], errors=[], error=str(e))
    return jsonify(path=path, rows=found["rows"], errors=found["errors"], error="")


@app.post("/api/targets")
def targets_set():
    """목표 파일 경로를 저장한다(경로만. 읽어 보고 읽을 수 있을 때만 저장, 비우면 지움). 읽은 결과를 돌려준다."""
    path = ((request.json or {}).get("path") or "").strip()
    if path:
        targets.read(path)
    settings.write(dash_calc.TARGETS_FILE, {"path": path})
    return targets_get()


@app.get("/api/dash/image")
def dash_image():
    """배치도 카드(map, 바탕 image)에 저장된 그림 파일 경로의 png·jpg·svg를 8MB까지 내려준다. 경로는 저장된 카드의 것만 쓰고(요청이 정하지 않는다) 그 밖의 파일은 주지 않는다.
    ?dash=대시보드 id&card=카드 id."""
    d = dash_store.get_dashboard(request.args.get("dash"))
    card = next((c for c in d["cards"] if c["id"] == request.args.get("card")), None)
    if card is None or card["kind"] != "map" or card["view"].get("bg") != "image" or not card["view"].get("image_path"):
        raise ValueError("그림 바탕이 정해진 배치도 카드가 아닙니다. 카드를 저장한 뒤 다시 여세요.")
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

RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}  # Windows가 파일 이름으로 쓰지 못하는 이름


def pdf_stem(name):
    """PDF 파일 이름(확장자 앞): 파일 이름에 못 쓰는 글자를 바꾸고 비면 '대시보드'."""
    stem = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", str(name or "")).strip(" .")[:80]
    return stem if stem and stem.upper() not in RESERVED else "대시보드"


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
    part.write_bytes(pdf)
    part.replace(path)  # 임시 이름으로 쓴 뒤 바꿔치기(실패해도 원래 파일이 남는다)
    return jsonify(path=str(path))
