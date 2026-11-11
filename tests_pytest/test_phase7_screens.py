"""Phase 7(plans/phase-7.md) 화면 쪽: 새 컬럼 상자(newcol.js)의 새 폼 → 계산 줄, 문장 틀 e57·e36, 전처리 미리보기의 Polars 이 PC 사본.
데이터는 시험 안에서 지어낸 메모리 표다. 모두 기존 함수 수정((수정) 절)이 끝나야 통과하므로 xfail(strict=True, reason="phase-7 <항목>")이다."""
import datetime as dt
import json
import threading

import polars as pl
import pytest
from lazy import ibis

from test_r5_q2 import needs_node, vm_run

T = dt.datetime

# 폼 → 기대 줄
FORMS7 = {
    "ma_std": ({"how": "window", "kind": "moving_std", "col": "x", "size": "3행", "order": "t", "dir": "asc", "group": "u", "name": "sd"}, ["moving_std x 3 rows 기준 t 그룹 u 이름 sd"]),
    "ma_std_days": ({"how": "window", "kind": "moving_std", "col": "x", "size": "7일", "order": "t", "dir": "asc", "group": "", "name": "sd2"}, ["moving_std x 7 days 기준 t 이름 sd2"]),
    "ewma": ({"how": "window", "kind": "ewma", "col": "x", "span": "3", "order": "t", "dir": "asc", "group": "u", "name": "w"}, ["ewma x 3 기준 t 그룹 u 이름 w"]),
    "session": ({"how": "behavior", "kind": "session", "user": "u", "time": "t", "minutes": "30", "name": "세션"}, ["session_id t 30 그룹 u 이름 세션"]),
    "gap": ({"how": "behavior", "kind": "gap", "user": "u", "time": "t", "unit": "minute", "name": "g"}, ["since_prev t minute 기준 t 그룹 u 이름 g"]),
    "next": ({"how": "behavior", "kind": "next", "user": "u", "time": "t", "unit": "hour", "name": "nx"}, ["until_next t hour 기준 t 그룹 u 이름 nx"]),
    "episode": ({"how": "behavior", "kind": "episode", "user": "u", "time": "t", "event": "e", "values": "로그인, 결제 완료", "at": "start", "text": True, "name": "ep"},
                ["식 episode_id(e, 'start', '로그인', '결제 완료') 기준 t 그룹 u 이름 ep"]),
    "streak_days": ({"how": "behavior", "kind": "streak_days", "user": "u", "time": "t", "name": "sk"}, ["streak t days 그룹 u 이름 sk"]),
    "streak_flag": ({"how": "behavior", "kind": "streak_flag", "user": "u", "time": "t", "cond": "x > 1", "name": "sf"}, ["식 x > 1 이름 sf_ok", "streak sf_ok flag 기준 t 그룹 u 이름 sf"]),
    "path": ({"how": "behavior", "kind": "path", "user": "u", "time": "t", "event": "e", "dedupe": "yes", "limit": "0", "name": "p"}, ["event_path e yes 0 기준 t 그룹 u 이름 p"]),
    "distinct": ({"how": "behavior", "kind": "distinct", "user": "u", "time": "t", "event": "e", "name": "k"}, ["cum_distinct e 기준 t 그룹 u 이름 k"]),
    "ewma_setting": ({"how": "window", "kind": "ewma", "col": "x", "span": "", "order": "t", "dir": "asc", "group": "", "name": "w2"}, ["ewma x 기준 t 이름 w2"]),  # 비우면 설정의 N
    "ewma_alpha": ({"how": "window", "kind": "ewma", "col": "x", "span": "0.3", "order": "t", "dir": "asc", "group": "", "name": "w3"}, ["ewma x 0.3 기준 t 이름 w3"]),
    "streak_weeks": ({"how": "behavior", "kind": "streak_days", "user": "u", "time": "t", "period": "weeks", "name": "sw"}, ["streak t weeks 그룹 u 이름 sw"]),
    "bucket": ({"how": "date", "kind": "bucket", "col": "t", "bn": "15", "name": "b"}, ["식 time_bucket(t, 15) 이름 b"]),
    "bucket_setting": ({"how": "date", "kind": "bucket", "col": "t", "bn": "", "name": "b2"}, ["식 time_bucket(t) 이름 b2"]),
    "tod": ({"how": "date", "kind": "part", "col": "t", "part": "time_of_day", "bounds": "", "labels": "", "name": "td"}, ["식 time_of_day(t) 이름 td"]),
    "tod_custom": ({"how": "date", "kind": "part", "col": "t", "part": "time_of_day", "bounds": "9, 18", "labels": "밤, 낮, 저녁", "name": "td2"},
                   ["식 time_of_day(t, [9, 18], ['밤', '낮', '저녁']) 이름 td2"]),
    "work": ({"how": "date", "kind": "part", "col": "t", "part": "work_hours", "start": "9", "end": "18", "days": "weekdays", "name": "wk"}, ["식 work_hours(t, 'weekdays', 9, 18) 이름 wk"]),
    "work_setting": ({"how": "date", "kind": "part", "col": "t", "part": "work_hours", "start": "", "end": "", "days": "all", "name": "wk2"}, ["식 work_hours(t, 'all') 이름 wk2"]),
    "wnum": ({"how": "date", "kind": "part", "col": "t", "part": "weekday_num", "name": "wn"}, ["식 weekday_num(t) 이름 wn"]),
    "center": ({"how": "group", "combo": "center", "col": "x", "groups": ["u", "", ""], "name": "c"}, ["그룹집계 mean(x) 그룹 u 이름 c_평균", "식 x - c_평균 이름 c"]),
}
BAD7 = {
    "labels_only": {"how": "date", "kind": "part", "col": "t", "part": "time_of_day", "bounds": "", "labels": "밤, 낮", "name": "x1"},
    "bad_limit": {"how": "behavior", "kind": "path", "user": "u", "time": "t", "event": "e", "dedupe": "yes", "limit": "-1", "name": "x2"},
    "bad_size": {"how": "window", "kind": "moving_std", "col": "x", "size": "3", "order": "t", "dir": "asc", "group": "", "name": "x3"},
}


def lines_of(tmp_path, forms):
    return vm_run(tmp_path, ["newcol.js"], f"(() => {{ const F = {json.dumps(forms, ensure_ascii=False)}, out = {{}};"
                  " for (const k in F) try { out[k] = NewCol.lines(F[k]); } catch (e) { out[k] = e.message; } return out; })()")


@needs_node
def test_newcol_lines_for_new_forms(tmp_path):
    got = lines_of(tmp_path, {k: v[0] for k, v in FORMS7.items()})
    for k, (_, want) in FORMS7.items():
        assert got[k] == want, k
    bad = lines_of(tmp_path, BAD7)
    assert "경계도" in bad["labels_only"] and "0 이상의 정수" in bad["bad_limit"] and "7행" in bad["bad_size"]


@needs_node
def test_newcol_new_lines_run_on_duckdb(tmp_path):
    from core import calc
    keys = ["session", "gap", "episode", "streak_days", "path", "distinct", "center", "bucket", "tod"]
    lines = lines_of(tmp_path, {k: FORMS7[k][0] for k in keys})
    t = ibis.memtable({"u": ["a", "a", "a", "b"], "x": [1.0, 2.0, 3.0, 4.0], "e": ["로그인", "상품", "로그인", "상품"],
                       "t": [T(2024, 1, 1, 0, 0), T(2024, 1, 1, 0, 10), T(2024, 1, 1, 2, 0), T(2024, 1, 1, 0, 0)]})
    out = {k: calc.apply(v, t).order_by("x").to_polars() for k, v in lines.items()}
    assert out["session"]["세션"].to_list() == [1, 1, 2, 1] and out["gap"]["g"].to_list() == [None, 10, 110, None]
    assert out["episode"]["ep"].to_list() == [1, 1, 2, 0] and out["streak_days"]["sk"].to_list() == [1, 1, 1, 1]
    assert out["path"]["p"].to_list() == ["로그인 -> 상품 -> 로그인"] * 3 + ["상품"] and out["distinct"]["k"].to_list() == [1, 2, 2, 1]
    assert out["center"]["c"].to_list() == [-1.0, 0.0, 1.0, 0.0]
    assert out["bucket"]["b"].to_list() == [T(2024, 1, 1, 0, 0), T(2024, 1, 1, 0, 0), T(2024, 1, 1, 2, 0), T(2024, 1, 1, 0, 0)]
    assert out["tod"]["td"].to_list() == ["새벽"] * 4


# ---------- 문장 틀 ----------

COLS = {"uid": "text", "ts": "date", "ev": "text", "amt": "num", "day": "date"}
E57 = [
    ({"종류": "세션 번호"}, "session_id ts 30 그룹 uid 이름 세션"),
    ({"종류": "직전 행동과의 간격", "단위": "second"}, "since_prev ts second 기준 ts 그룹 uid 이름 직전간격"),
    ({"종류": "다음 행동까지 시간"}, "until_next ts minute 기준 ts 그룹 uid 이름 다음까지"),
    ({"종류": "이벤트 기준 에피소드", "이벤트": "ev", "시작 값": ["로그인", "결제 완료"]}, "식 episode_id(col('ev'), 'start', '로그인', '결제 완료') 기준 ts 그룹 uid 이름 에피소드"),
    ({"종류": "연속 일수"}, "streak ts days 그룹 uid 이름 연속일수"),
    ({"종류": "경로 문자열", "이벤트": "ev"}, "event_path ev yes 0 기준 ts 그룹 uid 이름 경로"),
    ({"종류": "누적 고유 개수", "이벤트": "ev"}, "cum_distinct ev 기준 ts 그룹 uid 이름 누적고유"),
]


@pytest.mark.parametrize("slots, line", E57, ids=[s["종류"] for s, _ in E57])
def test_sentence_e57_builds_calc_lines(slots, line):
    from query import sentence_run as sr
    from query import sentences
    done = sr.compile("e57", {"사람": "uid", "시각": "ts", **slots}, {"base": "ev"}, COLS)
    assert done["ok"] and done["body"]["calcs"] == [line] and done["runs"][0]["route"] == "/api/query"
    assert {"session_id", "since_prev", "until_next", "episode_id", "streak", "event_path", "cum_distinct"} <= set(sentences.funcs_for("e57"))
    assert {"moving_std", "ewma"} <= set(sentences.funcs_for("e36"))
    ewma = sr.compile("e36", {"정렬 기준": "day", "값": "amt", "종류": "지수 가중 이동 평균", "N": 3}, {"base": "ev"}, COLS)
    assert ewma["ok"] and ewma["body"]["calcs"] == ["ewma amt 3 기준 day"]
    weekly = sr.compile("e36", {"정렬 기준": "day", "값": "amt", "종류": "이동 표준편차", "N": 3, "단위": "주"}, {"base": "ev"}, COLS)
    assert not weekly["ok"] and "‘행’ 단위로만" in weekly["reason"]


# ---------- 전처리 미리보기: 파일 연결(Polars)의 창 계산 줄은 이 PC 사본에서 ----------

def test_prep_preview_on_polars_uses_pc_copy_for_windows(client, monkeypatch):
    from core import held, server, source
    con = ibis.polars.connect()
    con.create_table("ev7", pl.DataFrame({"uid": ["u1", "u1", "u1", "u2", "u2"], "ts": [T(2026, 1, 5, 8, 3), T(2026, 1, 5, 8, 20), T(2026, 1, 5, 9, 30), T(2026, 1, 10, 13, 0), T(2026, 1, 10, 13, 10)]}))
    monkeypatch.setitem(server.state, "con", con)
    monkeypatch.setitem(server.state, "opened", {"kind": "folder", "files": []})
    monkeypatch.setitem(server.state, "cancel", threading.Event())
    try:
        got = client.post("/api/preview", json={"base": "ev7", "count": False, "formulas": [{"line": "since_prev ts minute 기준 ts 그룹 uid 이름 g"}]}).get_json()
        assert got["local"] is True  # 화면에 '이 PC 사본'으로 보인다
        assert sorted(r["g"] for r in got["rows"]["rows"] if r["g"] is not None) == [10, 17, 70]  # 행은 {컬럼: 값}
    finally:
        source.on_worker(held.clear)()
