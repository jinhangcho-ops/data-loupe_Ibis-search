"""기간 라우트(R5): 기간별 표 POST /api/period_table와, 지표 구하기 POST /api/metrics/run의 기간·비교(period·compare).
search_app 끝에서 불러와 같은 server.app에 붙는다(다른 *_app.py와 같은 방식)."""
from flask import jsonify, request

from core import held
from core import server
from core.server import app, get_con, run
from query.search_app import confirms

plain_metrics_run = app.view_functions["metrics_run"]  # search_app의 원래 라우트(period·compare가 없는 요청은 그대로 맡긴다)


@app.post("/api/period_table")
def period_table_view():
    """기간별 표: spec = {tables: [{schema?, base, date, label?, how, column?, split?}], unit, start?, end?} → {columns, rows, ratios}.
    원격 DB는 테이블마다 집계 SQL을 server.scan_gate로 처리 용량 확인한 뒤(넘으면 확인 응답) 구한다. 연결을 쓰므로 FREE가 아니다."""
    from metrics import period_table  # 화면을 열 때가 아니라 쓸 때 올린다(dashboard.period까지 따라오므로, test_lazy_loading_memory)
    body = request.json
    spec = period_table.check(body)
    con = get_con()
    if held.remote(con):
        for t in spec["tables"]:
            if ask := server.scan_gate(body, lambda t=t: server.expr_sql(con, period_table.grouped(spec, t))):
                return ask
    return jsonify(**run(lambda: period_table.build(spec)))


@confirms
def metrics_run_with_period():
    """POST /api/metrics/run: period({start, end, column?})나 compare를 주면 기간을 걸어(비교면 비교 기간으로 한 번 더) 구하고, 아니면 원래 라우트 그대로."""
    from metrics import period_compare  # 위와 같다
    body = request.json
    if not isinstance(body, dict) or not (body.get("period") or body.get("compare")):
        return plain_metrics_run()
    names = body.get("names")
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        raise ValueError("구할 지표를 고르세요.")
    return jsonify(**period_compare.compute(body, names, body.get("by"), body, body.get("period"), body.get("compare")))


app.view_functions["metrics_run"] = metrics_run_with_period  # 같은 주소·같은 이름의 라우트를 기간을 아는 것으로 바꿔 끼운다(search_app.py는 다른 묶음 소유)
