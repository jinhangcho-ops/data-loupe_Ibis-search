"""수집 예약(4단계)의 라우트. server.app에 달린다. 쿼리·분석 화면의 연결을 쓰지 않으므로 모두 차례(turn)를 기다리지 않는다."""
from flask import jsonify, request

import collect_app
import schedule
import server
from server import app

server.CONSTANTS["schedule"] = schedule.DEFAULT  # 수집 화면의 예약 칸 기본값
server.CONSTANTS["numbers"]["schedule.minutes"] = collect_app.number_range(int, *schedule.MINUTES)
server.FREE.update({"/api/schedules", "/api/schedules/run", "/api/schedules/reregister", "/api/runs", "/api/runs/seen"})


@app.get("/api/schedules")
def schedules():
    """예약, Windows 작업 상태(등록됐는지, 명령 경로가 지금 프로그램인지, 다음·마지막 실행), 프로그램 안 예약의 다음 시각, 경로가 맞지 않는 작업."""
    return jsonify(schedule.overview())


@app.post("/api/schedules")
def save_schedule():
    """schedule이 null이면 예약과 그 Windows 작업을 지운다."""
    schedule.save(request.json.get("name"), request.json.get("schedule"))
    return jsonify(schedule.overview())


@app.post("/api/schedules/run")
def run_schedule():
    schedule.run_now(request.json.get("name"))
    return jsonify(ok=True)


@app.post("/api/schedules/reregister")
def reregister():
    schedule.reregister()
    return jsonify(schedule.overview())


@app.get("/api/runs")
def runs():
    return jsonify(schedule.runs_view())


@app.post("/api/runs/seen")
def runs_seen():
    schedule.mark_seen()
    return jsonify(ok=True)
