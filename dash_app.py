"""대시보드(1단계)의 라우트. server.app에 달린다. 저장·목록·지우기·카드 더하기는 연결을 쓰지 않으므로 차례(turn)를 기다리지 않는다.
카드 값을 구하는 /api/dash/card/compute만 연결에서 구한다(차례를 기다린다)."""
import datetime as dt

from flask import jsonify, request

import dash_calc
import dash_store
import server
import source
from server import app

server.CONSTANTS["dash"] = {"max_cards": dash_store.MAX_CARDS, "max_dashboards": dash_store.MAX_DASHBOARDS, "max_palette": dash_store.MAX_PALETTE,
                            "types": dash_store.CHART_TYPES, "time_types": dash_store.TIME_TYPES, "units": dash_store.UNITS,
                            "palette": dash_store.DEFAULT_PALETTE, "locked": dash_store.LOCKED}  # 화면(dash.js)이 서버와 같은 상한·종류·기본 색을 쓴다
server.CONSTANTS["dash"]["max_points"] = dash_calc.MAX_POINTS  # 산점도·버블의 점 수 상한
server.FREE.update({"/api/dash", "/api/dash/delete", "/api/dash/card/add"})


def current_source():
    """지금 연결의 {key, label}(비밀번호·계정 이름 없음). 연결이 없으면 None."""
    if server.state["opened"] is None:
        return None
    recent = source.recent(server.state["opened"])
    return {"key": dash_store.source_key(recent), "label": dash_store.source_label(recent)}


def with_source(card):
    """카드의 source에 지금 연결의 key·label을 넣은 사본(연결이 없으면 그대로)."""
    now = current_source()
    return {**card, "source": {**(card.get("source") or {}), **(now or {})}} if now else card


@app.get("/api/dash")
def dash_list():
    """목록 {dashboards: [{id, name, cards, updated}], locked}. locked면 이 PC에서 풀 수 없는 파일이다."""
    return jsonify(dash_store.listing())


@app.get("/api/dash/<dash_id>")
def dash_get(dash_id):
    return jsonify(dashboard=dash_store.get_dashboard(dash_id))


@app.post("/api/dash")
def dash_save():
    """대시보드 하나를 저장한다(id가 없으면 새 대시보드). 저장한 모양과 새 목록을 돌려준다."""
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
    return jsonify(data=dash_calc.compute(card, dt.datetime.now().isoformat(timespec="seconds")), source=now)


@app.post("/api/chart/points")
def chart_points():
    """산점도·버블의 점: body = 차트 조회(테이블·조건 …, x, series[0].y 또는 y, split)와 size(버블의 크기 컬럼). 점·전체 수·뽑았는지·r·추세선을 돌려준다.
    source_key가 있는데 지금 연결과 다르면 거부한다."""
    body = request.json
    now = current_source()
    if now and body.get("source_key") and body["source_key"] != now["key"]:
        raise ValueError("이 카드는 다른 연결에서 구한 것입니다. 그 연결로 바꾼 뒤 다시 구하세요.")
    return jsonify(points=dash_calc.points_of(body, body.get("size")))
