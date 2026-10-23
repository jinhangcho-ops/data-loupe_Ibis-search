"""지표 사전의 계산(7-2): 항목(metric_catalog)의 calc 이름마다 함수 하나. KERNELS[calc](t, s, p, ctx)가 결과 {value, rows, cols, parts, dist, label, notes}를 돌려준다.
- t = 기간·조건까지 건 Ibis 표(큰 표는 Ibis가 집계하고 작은 결과만 PC로 가져온다), s = 역할 이름({user, time, amount, …})으로 정리한 빈칸 값, p = 항목이 정한 고정 값(어느 지표인지 등), ctx = 맥락(notes, other, align).
- value = 대표 값, rows·cols = 표(cols는 {키: 한글 이름}), parts = 비율 지표의 분자·분모 같은 짝 절대량({id: 값}), dist = 평균형 지표의 바탕 분포(중앙값·P90·P99를 구할 배열), label = 대표 값이 무엇인지.
- 원격 DB에는 값을 읽는 조회만 보낸다(행 수를 미리 세지 않는다). 배열로 가져오는 곳은 상한(mc.MAX_FETCH_ROWS)+1행까지만 묻고, 넘으면 표본이라고 알린다.
- 비율은 0~1 분수다(화면이 format이 percent일 때 ×100로 보인다). 구할 수 없는 값은 None이다."""
import math

import numpy as np
from lazy import ibis

from core import funnel
from metrics import metric_calc as mc

KERNELS = {}
MAX_ROWS = 60  # 결과 표에 싣는 행 수(코호트·기간·항목). 넘으면 최근(또는 상위) 것만 싣고 알린다
MAX_POINTS = 400  # 생존 곡선에 싣는 점 수


def kernel(name):
    """calc 이름으로 계산 함수를 등록한다."""
    def add(fn):
        KERNELS[name] = fn
        return fn
    return add


# ---------- 작은 도구 ----------

def one(expr):
    """집계 식 하나의 결과 한 줄."""
    return mc.rows(expr)[0]


def num(x):
    """숫자(실수)로, 없으면 None."""
    return None if x is None else float(x)


def result(value=None, rows=None, cols=None, parts=None, dist=None, label="", notes=None):
    """계산 결과 한 벌(빠진 칸은 비운 값)."""
    return {"value": value, "rows": rows or [], "cols": cols or {}, "parts": parts or {}, "dist": dist, "label": label, "notes": list(notes or [])}


def column_array(expr, name, ctx):
    """식의 숫자 컬럼을 numpy로(빈 값 제외, 상한까지). 행 수를 세지 않고 상한+1행까지만 묻는다."""
    got = expr.select(v=expr[name].cast("float64")).limit(mc.MAX_FETCH_ROWS + 1).to_pyarrow()["v"].to_numpy(zero_copy_only=False).astype(float)
    if len(got) > mc.MAX_FETCH_ROWS:
        got = got[:mc.MAX_FETCH_ROWS]
        ctx.notes.append(f"값이 {mc.MAX_FETCH_ROWS:,}개를 넘어 일부만 써요(중앙값·P90·P99는 표본 기준).")
    return got[~np.isnan(got)]


def with_user(t, s):
    """사용자가 비어 있지 않은 행만."""
    return t.filter(t[s["user"]].notnull())


def event_mask(t, s, event="event", value="event_value"):
    """이벤트 컬럼과 값이 있으면 '그 이벤트인 행' 참거짓 식, 없으면 None."""
    if s.get(event) and s.get(value) not in (None, ""):
        return t[s[event]] == funnel.step_value(t[s[event]].type(), s[value])
    return None


def only_event(t, s, event="event", value="event_value"):
    """이벤트 컬럼과 값이 있으면 그 이벤트의 행만."""
    mask = event_mask(t, s, event, value)
    return t if mask is None else t.filter(mask)


def with_time(t, s):
    """사용자·시각이 비어 있지 않은 행만(시각이 빈 행은 기간 번호를 못 만들어 가짜 사용자-날이 된다)."""
    return with_user(t, s).filter(t[s["time"]].notnull())


def label_of(index, unit):
    """버킷 번호의 첫 날 글자."""
    return mc.bucket_start(int(index), unit).isoformat()


def last_rows(rows, ctx, what="기간"):
    """표가 길면 최근 MAX_ROWS개만 싣는다."""
    if len(rows) > MAX_ROWS:
        ctx.notes.append(f"{what}이 많아 최근 {MAX_ROWS}개만 표에 실었어요(대표 값은 전체 기준).")
        return rows[-MAX_ROWS:]
    return rows


def top_rows(rows, ctx, what):
    """표가 길면 앞의 MAX_ROWS개(이미 많은 순으로 정렬된 것)만 싣고 알린다."""
    if len(rows) > MAX_ROWS:
        ctx.notes.append(f"{what}이 많아 앞의 {MAX_ROWS}개만 표에 실었어요(대표 값은 전체 기준).")
        return rows[:MAX_ROWS]
    return rows


def users_by_unit(t, s, unit, ctx):
    """기간 버킷마다 고유 사용자 수 → (표에 실을 [{period, users}], 첫~마지막 기간 칸 수, {기간 번호: 사용자 수}).
    표는 오래된 것부터 최근 MAX_ROWS칸만 만든다(활동 없는 기간은 0명). 칸이 아주 많아도(날짜 이상값) 칸마다 dict를 만들지 않으니, 평균 등은 칸 수·found로 계산한다."""
    act = mc.active_buckets(t, s["user"], s["time"], unit)
    found = {int(r["p"]): int(r["n"]) for r in mc.fetch_rows(act.group_by("p").aggregate(n=act.u.count()), "기간 수")}
    if not found:
        return [], 0, found
    lo, hi = min(found), max(found)
    cells = hi - lo + 1
    if cells > MAX_ROWS:
        ctx.notes.append(f"기간이 많아 최근 {MAX_ROWS}개만 표에 실었어요(대표 값은 전체 기준).")
    msg = mc.sparse_note(cells, len(found))
    if msg:
        ctx.notes.append(msg)
    return [{"period": label_of(p, unit), "users": found.get(p, 0)} for p in range(max(lo, hi - MAX_ROWS + 1), hi + 1)], cells, found


# ---------- 1 트래픽·활성도 ----------

@kernel("uv")
def uv(t, s, p, ctx):
    """UV: 고유 사용자 수(단위를 주면 기간별 표도)."""
    if s.get("unit") and not s.get("time"):
        raise ValueError("단위별로 보려면 시각 컬럼을 골라 주세요.")
    x = with_user(t, s)
    n = int(one(x.aggregate(n=x[s["user"]].nunique()))["n"])
    if not s.get("unit"):
        return result(n, parts={"uv": n}, label="고유 사용자 수")
    rows = users_by_unit(t, s, s["unit"], ctx)[0]
    return result(n, rows, {"period": "기간", "users": "고유 사용자 수"}, {"uv": n})


@kernel("pv")
def pv(t, s, p, ctx):
    """PV: 이벤트(조회) 행 수."""
    x = only_event(t, s)
    n = int(one(x.aggregate(n=x.count()))["n"])
    return result(n, parts={"pv": n}, label="조회(이벤트) 수")


@kernel("pv_per_uv")
def pv_per_uv(t, s, p, ctx):
    """PV/UV: 사용자 1명당 조회 수. 분포는 사용자별 조회 수."""
    t = with_user(t, s)
    mask = event_mask(t, s)
    per = t.group_by(s["user"]).aggregate(n=t.count() if mask is None else mask.cast("int64").sum())  # 조회가 0번인 사용자도 분포에 든다
    r = one(per.aggregate(views=per.n.sum(), users=per.count()))
    views, users = int(r["views"] or 0), int(r["users"])
    return result(mc.ratio(views, users), parts={"pv": views, "uv": users}, dist=column_array(per, "n", ctx), label="사용자 1명당 조회 수")


@kernel("active")
def active(t, s, p, ctx):
    """DAU·WAU·MAU: 기간 버킷마다 고유 사용자 수의 평균(활동 없는 기간은 0명). 분포는 기간별 값."""
    rows, cells, found = users_by_unit(t, s, p["unit"], ctx)
    if not cells:
        return result(None, [], {"period": "기간", "users": "활성 사용자 수"}, dist=np.array([], float), label=f"{p['unit_name']} 평균")
    values = np.concatenate([np.fromiter(found.values(), float, len(found)), np.zeros(cells - len(found))])  # 활동 없는 기간은 0명
    return result(sum(found.values()) / cells, rows, {"period": "기간", "users": "활성 사용자 수"}, dist=values, label=f"{p['unit_name']} 평균")


@kernel("stickiness")
def stickiness(t, s, p, ctx):
    """고착도: 달마다 (그 달 일별 활동자 평균) ÷ MAU, 달들의 평균."""
    x = t.select(u=t[s["user"]], d=mc.day_index(t[s["time"]]), m=mc.bucket_index(t[s["time"]], "month"))
    x = x.filter(x.u.notnull(), x.d.notnull()).distinct()
    days = {}
    for r in mc.rows(x.group_by(["m", "d"]).aggregate(n=x.u.count())):
        days.setdefault(int(r["m"]), []).append(int(r["n"]))
    mau = {int(r["m"]): int(r["n"]) for r in mc.rows(x.group_by("m").aggregate(n=x.u.nunique()))}
    rows = [{"period": label_of(m, "month"), "dau": sum(v) / len(v), "mau": mau[m], "stickiness": mc.ratio(sum(v) / len(v), mau[m])} for m, v in sorted(days.items())]
    ctx.notes.append("DAU는 그 달에 활동이 있는 날들의 평균이고 활동이 없는 날·달은 평균에서 빠져서, 띄엄띄엄 쓰는 서비스는 고착도가 실제보다 높게 나와요.")
    return result(mc.mean_defined([r["stickiness"] for r in rows]), last_rows(rows, ctx), {"period": "월", "dau": "DAU(평균)", "mau": "MAU", "stickiness": "DAU/MAU"},
                  {"dau": mc.mean_defined([r["dau"] for r in rows]), "mau": mc.mean_defined([r["mau"] for r in rows])}, label="월별 평균")


@kernel("concurrency")
def concurrency(t, s, p, ctx):
    """PCU·ACU·MCU: 시작·끝 구간 표의 동시 접속(최대·시간 가중 평균·달별 최대)."""
    times, counts = mc.concurrency_series(t, s["start"], s["end"])
    if p["pick"] == "mcu":
        peaks = mc.monthly_peak(times, counts)
        rows = [{"period": m, "mcu": v} for m, v in peaks.items()]
        return result(max(peaks.values()) if peaks else None, last_rows(rows, ctx, "달"), {"period": "월", "mcu": "월 최대 동시 접속"}, label="가장 큰 달")
    stats = mc.concurrency_stats(times, counts)
    return result(stats[p["pick"]], label="첫 변화부터 마지막 변화까지")


def sessions(t, s):
    """(세션 컬럼을 단 표, 세션마다 한 줄인 표 per(session, u, first, last, events, length, d))."""
    t = t.filter(t[s["user"]].notnull(), t[s["time"]].notnull())  # 사용자·시각이 빈 행은 한 세션으로 묶이거나 길이를 망가뜨리므로 뺀다
    ses = mc.add_sessions(t, s["user"], s["time"], gap_minutes=s.get("gap"), key=s.get("sess_cols") or None)
    per = ses.group_by("session").aggregate(u=ses[s["user"]].max(), first=ses[s["time"]].min(), last=ses[s["time"]].max(), events=ses.count())
    return ses, per.mutate(length=mc.epoch(per["last"]) - mc.epoch(per["first"]), d=mc.day_index(per["first"]))


@kernel("session_count")
def session_count(t, s, p, ctx):
    """세션 수 ÷ 접속한 사용자·날 수."""
    ses, per = sessions(t, s)
    n = int(one(per.aggregate(n=per.count()))["n"])
    days = per.group_by(["u", "d"]).aggregate(n=per.count())
    user_days = int(one(days.aggregate(n=days.count()))["n"])
    return result(mc.ratio(n, user_days), parts={"sessions": n, "user_days": user_days}, dist=column_array(days, "n", ctx), label="접속한 사용자·날 1개당 세션 수")


@kernel("session_length")
def session_length(t, s, p, ctx):
    """ASD: 세션 1개당 길이(초). 분포는 세션별 길이."""
    ses, per = sessions(t, s)
    r = one(per.aggregate(n=per.count(), total=per.length.sum()))
    n, total = int(r["n"]), num(r["total"]) or 0.0
    return result(mc.ratio(total, n), parts={"stay_seconds": total, "sessions": n}, dist=column_array(per, "length", ctx), label="세션 1개당 초")


@kernel("daily_stay")
def daily_stay(t, s, p, ctx):
    """TSD: 접속한 사용자·날 1개당 체류 시간(초). 분포는 사용자·날별 체류."""
    ses, per = sessions(t, s)
    days = per.group_by(["u", "d"]).aggregate(stay=per.length.sum())
    r = one(days.aggregate(n=days.count(), total=days.stay.sum()))
    n, total = int(r["n"]), num(r["total"]) or 0.0
    return result(mc.ratio(total, n), parts={"stay_seconds": total, "user_days": n}, dist=column_array(days, "stay", ctx), label="접속한 사용자·날 1개당 초")


@kernel("dau_split")
def dau_split(t, s, p, ctx):
    """DAU 분해: 활성을 신규·기존·복귀로(일 단위)."""
    return states(t, s, {"pick": "dau_split", "unit": "day"}, ctx)


# ---------- 2 퍼널·온보딩 ----------

@kernel("bounce")
def bounce(t, s, p, ctx):
    """이탈 세션 비율: 이벤트(또는 페이지 이벤트)가 하나뿐인 세션 ÷ 세션."""
    ses, per = sessions(t, s)
    if s.get("event") and s.get("event_value") not in (None, ""):
        hit = ses[s["event"]] == funnel.step_value(ses[s["event"]].type(), s["event_value"])
        per = ses.group_by("session").aggregate(events=hit.cast("int64").sum())
        per = per.filter(per.events > 0)
    r = one(per.aggregate(n=per.count(), b=(per.events == 1).cast("int64").sum()))
    n, b = int(r["n"]), int(r["b"] or 0)
    return result(mc.ratio(b, n), parts={"sessions": n, "bounces": b}, label="세션 중 한 번만 하고 나간 비율")


@kernel("exit_rate")
def exit_rate(t, s, p, ctx):
    """페이지별 이탈률과 전체 이탈률."""
    ses, _ = sessions(t, s)
    found = mc.exit_rates(ses, "session", s["time"], s["page"])
    views, exits = sum(r["views"] for r in found), sum(r["exits"] for r in found)
    rows = [{"page": str(r["page"]), "views": r["views"], "exits": r["exits"], "exit_rate": r["exit_rate"]} for r in top_rows(found, ctx, "페이지")]
    return result(mc.ratio(exits, views), rows, {"page": "페이지", "views": "조회 수", "exits": "마지막으로 본 세션 수", "exit_rate": "이탈률"},
                  {"views": views, "exits": exits}, label="전체 이탈률")


def between(t, s):
    """(사건 A → 사건 B 사이 초 배열, A를 한 사용자 수)."""
    return mc.seconds_between(t, s["user"], s["time"], s["event"], s["step_a"], s["step_b"])


@kernel("time_between")
def time_between(t, s, p, ctx):
    """사건 A 이후 첫 B까지 걸린 시간(초)의 평균. 분포는 사용자별 시간."""
    values, first = between(t, s)
    return result(float(values.mean()) if len(values) else None, parts={"users_first": first, "users_reached": int(len(values))}, dist=values, label="평균(초)")


@kernel("conversion")
def conversion(t, s, p, ctx):
    """A를 한 사용자 중 B까지 간 비율(invert면 못 간 비율)."""
    values, first = between(t, s)
    rate = mc.ratio(len(values), first)
    value = rate if not p.get("invert") or rate is None else 1 - rate
    return result(value, parts={"users_first": first, "users_reached": int(len(values))}, label="A를 한 사용자 중 B까지 간 비율" if not p.get("invert") else "A를 한 사용자 중 B에 가지 못한 비율")


@kernel("ftue")
def ftue(t, s, p, ctx):
    """첫 경험 퍼널: 단계별 도달과 마지막 단계까지의 비율."""
    found = funnel.compute(t, s["user"], s["event"], s["time"], s["steps"], s.get("limit_sec"))["steps"]
    rows = [{"step": r["name"], "users": r["users"], "conv": r["conv"], "from_first": r["from_first"], "drop": r["drop"]} for r in found]
    return result(found[-1]["from_first"], rows, {"step": "단계", "users": "사용자 수", "conv": "이전 단계 대비", "from_first": "첫 단계 대비", "drop": "빠진 사용자 수"},
                  {"first_step_users": found[0]["users"], "last_step_users": found[-1]["users"]}, label="마지막 단계까지 간 비율")


# ---------- 3 리텐션·코호트 ----------

@kernel("retention")
def retention(t, s, p, ctx):
    """클래식·롤링·범위 리텐션: 코호트별 표와 코호트 크기로 가중한 평균."""
    mode, unit, n = p["mode"], s.get("unit") or "day", s.get("n") or 7
    span = (s["from_n"], s["to_n"]) if mode == "range" else None
    m = mc.cohort_matrix(t, s["user"], s["time"], unit, mode, n_max=n, span=span)
    curve = mc.average_curve(m["cohorts"])
    cols = {"cohort": "코호트", "size": "크기", **{f"d{i}": f"{unit[0].upper() if isinstance(unit, str) else 'D'}{i}" for i in range(len(curve))}}
    rows = [{"cohort": c["cohort"], "size": c["size"], **{f"d{i}": v for i, v in enumerate(c["values"])}} for c in m["cohorts"]]
    value = curve[0] if mode == "range" else (curve[n] if n < len(curve) else None)
    if mode == "range":
        cols = {"cohort": "코호트", "size": "크기", "d0": f"{span[0]}~{span[1]}"}
    return result(value, last_rows(rows, ctx, "코호트"), cols, {"cohort_size": sum(c["size"] for c in m["cohorts"])}, label="코호트 크기로 가중한 평균")


@kernel("flattening")
def flattening(t, s, p, ctx):
    """유지율 곡선이 수평이 되는 첫 N."""
    unit = s.get("unit") or "day"
    m = mc.cohort_matrix(t, s["user"], s["time"], unit, "classic", n_max=s["n"])
    curve = mc.average_curve(m["cohorts"])
    point = mc.flattening_point(curve, eps=s["eps"] / 100, run=s["run"])
    if point is None:
        ctx.notes.append("관찰한 기간 안에서는 유지율이 수평이 되는 곳을 찾지 못했어요. N을 늘리거나 허용 오차를 키워 보세요.")
    return result(point, [{"n": i, "retention": v} for i, v in enumerate(curve)], {"n": "경과 기간 N", "retention": "평균 유지율"}, {"cohort_size": sum(c["size"] for c in m["cohorts"])}, label="유지율이 수평이 되기 시작하는 N")


@kernel("states")
def states(t, s, p, ctx):
    """사용자 상태 지표(DAU 분해·Churn·Resurrection·Quick Ratio)."""
    unit = s.get("unit") or p.get("unit") or "month"
    found = mc.user_states(t, s["user"], s["time"], unit, s.get("inactive_days"), ctx.notes)
    pick = p["pick"]
    shown = {"dau_split": ("active", "new", "retained", "resurrected"), "churn": ("active", "churned", "churn_rate"), "resurrection": ("active", "resurrected", "resurrection_rate"),
             "quick": ("new", "resurrected", "churned", "quick_ratio")}[pick]
    names = {"active": "활동자", "new": "신규", "retained": "기존", "resurrected": "복귀", "churned": "이탈", "churn_rate": "이탈률", "resurrection_rate": "복귀율", "quick_ratio": "Quick Ratio"}
    key = {"dau_split": "active", "churn": "churn_rate", "resurrection": "resurrection_rate", "quick": "quick_ratio"}[pick]
    parts = {"churned": sum(r["churned"] or 0 for r in found), "new": sum(r["new"] for r in found), "resurrected": sum(r["resurrected"] for r in found)}
    rows = [{"period": r["period"], **{k: r[k] for k in shown}} for r in found]
    return result(mc.mean_defined([r[key] for r in found]), last_rows(rows, ctx), {"period": "기간", **{k: names[k] for k in shown}}, parts, label="기간 평균")


@kernel("power_curve")
def power_curve(t, s, p, ctx):
    """최근 L일 중 접속 일수의 분포."""
    window = s["span"]
    x = t.select(u=t[s["user"]], d=mc.day_index(t[s["time"]]))
    x = x.filter(x.u.notnull(), x.d.notnull()).distinct()
    last = one(x.aggregate(m=x.d.max()))["m"]
    if last is None:
        return result(None)
    w = x.filter(x.d > last - window)
    per = w.group_by("u").aggregate(days=w.d.count())
    found = {int(r["days"]): int(r["users"]) for r in mc.rows(per.group_by("days").aggregate(users=per.count()))}
    total = sum(found.values())
    rows = [{"days": d, "users": found.get(d, 0), "share": mc.ratio(found.get(d, 0), total)} for d in range(1, window + 1)]
    mean = mc.ratio(sum(d * c for d, c in found.items()), total)
    return result(mean, rows, {"days": f"최근 {window}일 중 접속 일수", "users": "사용자 수", "share": "비중"}, {"active_users": total}, label=f"최근 {window}일 사용자 1명당 평균 접속 일수")


@kernel("survival")
def survival(t, s, p, ctx):
    """Kaplan-Meier 생존 곡선과 중앙 생존 시간."""
    x = t.select(u=t[s["user"]], d=mc.day_index(t[s["time"]]))
    per = x.filter(x.u.notnull(), x.d.notnull()).group_by("u").aggregate(first=x.d.min(), last=x.d.max())
    end = one(per.aggregate(m=per.last.max()))["m"]
    if end is None:
        return result(None)
    per = per.select(dur=per["last"] - per["first"], gone=(end - per["last"]) >= s["churn_days"])
    got = per.limit(mc.MAX_FETCH_ROWS + 1).to_pyarrow()
    if got.num_rows > mc.MAX_FETCH_ROWS:
        got = got.slice(0, mc.MAX_FETCH_ROWS)
        ctx.notes.append(f"사용자가 {mc.MAX_FETCH_ROWS:,}명을 넘어 일부만 썼어요(표본 기준).")
    km = mc.kaplan_meier(got["dur"].to_numpy(zero_copy_only=False), got["gone"].to_numpy(zero_copy_only=False))
    points = list(zip(km["times"], km["survival"], km["at_risk"], km["events"]))
    if len(points) > MAX_POINTS:
        step = math.ceil(len(points) / MAX_POINTS)
        points = points[::step]
        ctx.notes.append(f"점이 많아 {MAX_POINTS}개 안팎으로 줄여 실었어요.")
    ctx.notes.append(f"마지막 활동이 자료의 끝보다 {s['churn_days']}일 이상 앞서면 떠난 것(사건), 아니면 아직 남은 것(중도절단)으로 봤어요.")
    return result(km["median"], [{"t": a, "survival": b, "at_risk": c, "events": d} for a, b, c, d in points], {"t": "머문 일수 t", "survival": "S(t)", "at_risk": "남은 수", "events": "떠난 수"},
                  {"users": int(got.num_rows)}, label="생존율이 50% 이하가 되는 첫 일수(중앙 생존 시간)")


# ---------- 4 수익화·LTV ----------

def paid(t, s):
    """금액이 0보다 큰 행(사용자가 있는 것)."""
    return t.filter(t[s["user"]].notnull(), t[s["amount"]] > 0)


@kernel("revenue")
def revenue(t, s, p, ctx):
    """총매출·순매출(수수료·부가세·환불 상수 입력)."""
    got = one(t.aggregate(g=t[s["amount"]].sum(), **({"r": t[s["refund"]].sum()} if s.get("refund") else {})))
    gross, refund = num(got["g"]) or 0.0, num(got.get("r")) or 0.0
    if p["pick"] == "gross":
        return result(gross, parts={"gross_revenue": gross}, label="금액 합계")
    net = (gross - refund) / (1 + s["vat"] / 100) * (1 - s["fee"] / 100)
    ctx.notes.append(f"순매출 = (총매출 − 환불) ÷ (1 + 부가세 {s['vat']:g}%) × (1 − 수수료 {s['fee']:g}%)")
    return result(net, parts={"gross_revenue": gross, "refund": refund}, label="수수료·환불·부가세를 뺀 매출")


@kernel("payers")
def payers(t, s, p, ctx):
    """PU: 금액이 0보다 큰 고유 사용자 수."""
    x = paid(t, s)
    n = int(one(x.aggregate(n=x[s["user"]].nunique()))["n"])
    return result(n, parts={"pu": n}, label="결제한 사용자 수")


@kernel("new_payers")
def new_payers(t, s, p, ctx):
    """NPA: 기간마다 처음 결제한 사용자 수."""
    unit = s["unit"]
    x = paid(t, s)
    x = x.filter(x[s["time"]].notnull()).select(u=x[s["user"]], p=mc.bucket_index(x[s["time"]], unit))
    first = x.group_by("u").aggregate(p=x.p.min())
    rows = [{"period": label_of(r["p"], unit), "npa": int(r["n"])} for r in mc.rows(first.group_by("p").aggregate(n=first.u.count()).order_by("p"))]
    ctx.notes.append("고른 기간 앞의 결제 기록은 보이지 않아, 기간 안에서 처음 결제한 사용자를 신규로 세요.")
    return result(rows[-1]["npa"] if rows else 0, last_rows(rows, ctx), {"period": "기간", "npa": "신규 결제자 수"}, {"pu": sum(r["npa"] for r in rows)}, label="가장 최근 기간")


@kernel("pur")
def pur(t, s, p, ctx):
    """PUR(DPUR·MPUR): 기간마다 결제자 ÷ 활동자."""
    unit = p["unit"] if p.get("unit") else s["unit"]
    x = with_time(t, s)
    x = x.select(u=x[s["user"]], p=mc.bucket_index(x[s["time"]], unit), a=x[s["amount"]])
    active_n = {int(r["p"]): int(r["n"]) for r in mc.rows(x.group_by("p").aggregate(n=x.u.nunique()))}
    y = x.filter(x.a > 0)
    paid_n = {int(r["p"]): int(r["n"]) for r in mc.rows(y.group_by("p").aggregate(n=y.u.nunique()))}
    rows = [{"period": label_of(k, unit), "payers": paid_n.get(k, 0), "users": v, "pur": mc.ratio(paid_n.get(k, 0), v)} for k, v in sorted(active_n.items())]
    total_users = int(one(x.aggregate(n=x.u.nunique()))["n"])
    total_payers = int(one(y.aggregate(n=y.u.nunique()))["n"])
    return result(mc.mean_defined([r["pur"] for r in rows]), last_rows(rows, ctx), {"period": "기간", "payers": "결제자", "users": "활동자", "pur": "결제 비율"},
                  {"pu": total_payers, "uv": total_users}, label="기간별 결제 비율의 평균")


@kernel("arpu")
def arpu(t, s, p, ctx):
    """ARPU: 활동 사용자 1명당 매출. 분포는 사용자별 매출."""
    x = with_user(t, s)
    per = x.group_by(s["user"]).aggregate(v=x[s["amount"]].fill_null(0).sum())
    r = one(per.aggregate(rev=per.v.sum(), uv=per.count()))
    rev, users = num(r["rev"]) or 0.0, int(r["uv"])
    return result(mc.ratio(rev, users), parts={"gross_revenue": rev, "uv": users}, dist=column_array(per, "v", ctx), label="활동 사용자 1명당 매출")


@kernel("arppu")
def arppu(t, s, p, ctx):
    """ARPPU: 결제자 1명당 매출. 분포는 결제자별 매출."""
    x = paid(t, s)
    per = x.group_by(s["user"]).aggregate(v=x[s["amount"]].sum())
    r = one(per.aggregate(rev=per.v.sum(), pu=per.count()))
    rev, pu = num(r["rev"]) or 0.0, int(r["pu"])
    return result(mc.ratio(rev, pu), parts={"gross_revenue": rev, "pu": pu}, dist=column_array(per, "v", ctx), label="결제자 1명당 매출")


@kernel("arpdau")
def arpdau(t, s, p, ctx):
    """ARPDAU: 매출 ÷ 일별 활동자의 합. 분포는 사용자·날별 매출."""
    x = with_time(t, s)
    days = x.group_by([s["user"], mc.day_index(x[s["time"]]).name("d")]).aggregate(v=x[s["amount"]].fill_null(0).sum())
    r = one(days.aggregate(rev=days.v.sum(), n=days.count()))
    rev, n = num(r["rev"]) or 0.0, int(r["n"])
    return result(mc.ratio(rev, n), parts={"gross_revenue": rev, "user_days": n}, dist=column_array(days, "v", ctx), label="접속한 사용자·날 1개당 매출(합계 ÷ DAU의 합)")


@kernel("ltv")
def ltv(t, s, p, ctx):
    """LTV: 코호트 N일 누적 매출 ÷ 코호트 크기 또는 월 ARPU ÷ 월 이탈률."""
    n_days, unit = s["n_days"], s.get("unit") or "day"
    if s["method"] == "ARPU÷이탈률":
        found = mc.user_states(t, s["user"], s["time"], "month", notes=ctx.notes)
        churn = mc.mean_defined([r["churn_rate"] for r in found])
        x = with_user(t, s)
        per = x.select(u=x[s["user"]], m=mc.bucket_index(x[s["time"]], "month"), a=x[s["amount"]].fill_null(0))
        r = one(per.aggregate(rev=per.a.sum()))
        both = per.select("u", "m").distinct()
        user_months = int(one(both.aggregate(n=both.count()))["n"])
        arpu_month = mc.ratio(num(r["rev"]) or 0.0, user_months)
        value = mc.ratio(arpu_month, churn)
        ctx.notes.append("LTV = 월 ARPU(사용자·월 1개당 매출) ÷ 월 이탈률. 이탈률이 0이면 구할 수 없어요.")
        return result(value, parts={"arpu_month": arpu_month, "churn_rate": churn}, label="ARPU ÷ 이탈률")
    cohorts = mc.ltv(t, s["user"], s["time"], s["amount"], n_days, unit)
    done = [c for c in cohorts if c["complete"]]
    use = done or cohorts
    if not done and cohorts:
        ctx.notes.append(f"{n_days}일이 다 지난 코호트가 없어 지금까지의 값으로 계산했어요.")
    size, rev = sum(c["size"] for c in use), sum(c["revenue"] for c in use)
    rows = [{"cohort": c["cohort"], "size": c["size"], "revenue": c["revenue"], "ltv": c["ltv"], "complete": c["complete"]} for c in cohorts]
    return result(mc.ratio(rev, size), last_rows(rows, ctx, "코호트"), {"cohort": "코호트", "size": "크기", "revenue": f"{n_days}일 매출", "ltv": f"LTV{n_days}", "complete": "관찰 완료"},
                  {"cohort_size": size, "gross_revenue": rev}, label=f"{n_days}일 안 매출 ÷ 코호트 크기(관찰이 끝난 코호트)")


@kernel("repeat")
def repeat(t, s, p, ctx):
    """재구매율: N번 이상 산 사용자 ÷ 구매 사용자."""
    x = paid(t, s)
    count = x[s["orders"]].nunique() if s.get("orders") else x.count()
    per = x.group_by(s["user"]).aggregate(n=count)
    r = one(per.aggregate(buyers=per.count(), again=(per.n >= s["n"]).cast("int64").sum()))
    buyers, again = int(r["buyers"]), int(r["again"] or 0)
    return result(mc.ratio(again, buyers), parts={"pu": buyers, "repeat_buyers": again}, label=f"결제자 중 {s['n']}번 이상 산 비율")


@kernel("tiers")
def tiers(t, s, p, ctx):
    """Whale·Dolphin·Minnow 등급별 사용자·매출 비중(분위수 또는 금액 기준)."""
    x = paid(t, s)
    per = x.group_by(s["user"]).aggregate(v=x[s["amount"]].sum())
    values = column_array(per, "v", ctx)
    if len(values) == 0:
        return result(None)
    if s["mode"] == "분위수":
        whale, dolphin = float(np.quantile(values, 1 - s["whale"] / 100)), float(np.quantile(values, 1 - s["dolphin"] / 100))
    else:
        whale, dolphin = float(s["whale"]), float(s["dolphin"])
    found = mc.tier_shares(values, whale, dolphin)
    names = {"whale": "Whale", "dolphin": "Dolphin", "minnow": "Minnow"}
    rows = [{"tier": names[k], "users": v["users"], "user_share": v["user_share"], "total": v["total"], "total_share": v["total_share"]} for k, v in found.items()]
    ctx.notes.append(f"기준 금액: Whale {whale:,.0f} 이상, Dolphin {dolphin:,.0f} 이상, 그 아래는 Minnow(결제자 기준).")
    return result(found["whale"]["total_share"], rows, {"tier": "등급", "users": "사용자 수", "user_share": "사용자 비중", "total": "매출", "total_share": "매출 비중"},
                  {"pu": int(len(values))}, label="Whale의 매출 비중")


@kernel("recurring")
def recurring(t, s, p, ctx):
    """MRR(월별 금액 합, 마지막 달)·ARR(×12)."""
    x = t.select(m=mc.bucket_index(t[s["time"]], "month"), a=t[s["amount"]])
    rows = [{"period": label_of(r["m"], "month"), "mrr": num(r["v"])} for r in mc.rows(x.group_by("m").aggregate(v=x.a.sum()).order_by("m")) if r["m"] is not None]
    last = rows[-1]["mrr"] if rows else None
    value = last if p["pick"] == "mrr" else (None if last is None else last * 12)
    return result(value, last_rows(rows, ctx, "달"), {"period": "월", "mrr": "월 반복 매출"}, {"mrr": last}, label="가장 최근 달" if p["pick"] == "mrr" else "가장 최근 달 × 12")


@kernel("nrr")
def nrr(t, s, p, ctx):
    """NRR: 지난달 매출이 있던 고객의 이번 달 매출 ÷ 지난달 매출."""
    found = mc.nrr(t, s["user"], s["time"], s["amount"])
    rows = [{"period": r["month"], "prev_revenue": r["prev_revenue"], "kept_revenue": r["kept_revenue"], "nrr": r["nrr"]} for r in found]
    last = found[-1] if found else None
    return result(last["nrr"] if last else None, last_rows(rows, ctx, "달"), {"period": "월", "prev_revenue": "지난달 매출", "kept_revenue": "그 고객의 이번 달 매출", "nrr": "NRR"},
                  {"prev_revenue": last["prev_revenue"] if last else None, "kept_revenue": last["kept_revenue"] if last else None}, label="가장 최근 달")


# ---------- 5 마케팅·UA ----------

@kernel("impressions")
def impressions(t, s, p, ctx):
    """노출 수 합계(컬럼이 없으면 행 수)."""
    n = num(one(t.aggregate(n=t[s["impressions"]].sum() if s.get("impressions") else t.count()))["n"]) or 0.0
    return result(n, parts={"impressions": n}, label="노출 수")


@kernel("reach")
def reach(t, s, p, ctx):
    """도달: 고유 사용자 수."""
    x = with_user(t, s)
    n = int(one(x.aggregate(n=x[s["user"]].nunique()))["n"])
    return result(n, parts={"reach": n}, label="노출된 고유 사용자 수")


@kernel("frequency")
def frequency(t, s, p, ctx):
    """노출 빈도: 노출 수 ÷ 도달. 분포는 사용자별 노출 수."""
    x = with_user(t, s)
    per = x.group_by(s["user"]).aggregate(n=x[s["impressions"]].sum() if s.get("impressions") else x.count())
    r = one(per.aggregate(imp=per.n.sum(), reach=per.count()))
    imp, users = num(r["imp"]) or 0.0, int(r["reach"])
    return result(mc.ratio(imp, users), parts={"impressions": imp, "reach": users}, dist=column_array(per, "n", ctx), label="사용자 1명당 노출 수")


@kernel("ad_ratio")
def ad_ratio(t, s, p, ctx):
    """광고 효율 비율(CTR·IPM·CPM·CPC·CPI·CPA): 분자 합 ÷ 분모 합 × 배율."""
    got = one(t.aggregate(a=t[s[p["num"]]].sum(), b=t[s[p["den"]]].sum()))
    a, b = num(got["a"]) or 0.0, num(got["b"]) or 0.0
    value = mc.ratio(a, b)
    return result(None if value is None else value * p.get("scale", 1), parts={p["num"]: a, p["den"]: b}, label=p["label"])


@kernel("kfactor")
def kfactor(t, s, p, ctx):
    """K-Factor: 사용자당 보낸 초대 수 × 초대 전환율."""
    x = with_user(t, s)
    r = one(x.aggregate(users=x[s["user"]].nunique(), sent=x[s["invites"]].sum(), joined=x[s["joined"]].sum()))
    users, sent, joined = int(r["users"]), num(r["sent"]) or 0.0, num(r["joined"]) or 0.0
    per_user, conv = mc.ratio(sent, users), mc.ratio(joined, sent)
    return result(None if per_user is None or conv is None else per_user * conv, parts={"invites": sent, "joined": joined, "uv": users}, label="초대 수/사용자 × 초대 전환율")


@kernel("organic_uplift")
def organic_uplift(t, s, p, ctx):
    """Organic Uplift: 오가닉 신규 ÷ 유료 신규(사용자의 처음 행의 유입 경로 기준)."""
    x = with_user(t, s)
    first = x.group_by(s["user"]).aggregate(src=x[s["source"]].argmin(x[s["time"]]))
    is_paid = first.src == funnel.step_value(x[s["source"]].type(), s["paid_value"])
    r = one(first.aggregate(paid=is_paid.cast("int64").sum(), total=first.count()))
    paid_n, total = int(r["paid"] or 0), int(r["total"])
    organic = total - paid_n
    return result(mc.ratio(organic, paid_n), parts={"paid_installs": paid_n, "organic_installs": organic}, label="유료로 온 신규 1명당 오가닉으로 온 신규 수")


# ---------- 5 마케팅·UA: 두 테이블(광고비 테이블 + 사용자 테이블) ----------
# 광고비 쪽과 사용자 쪽을 같은 키(기간 버킷 또는 공통 키)로 묶어 각각 작게 집계한 뒤 Python에서 맞춘다(두 큰 표를 조인하지 않는다).

def check_align(align):
    """맞춤 구절(my_metrics.check_align과 같은 모양)이 있는지 본다. 없으면 이유와 함께 거부."""
    if not align:
        raise ValueError("광고비 테이블의 값을 쓰는 지표는 두 테이블을 맞추는 방법을 골라야 해요: ‘기간 단위로 맞춰’(일·주·월) 또는 ‘공통 키로 맞춰’(공통 키 찾기에서 고른 컬럼 짝).")
    if align.get("kind") == "period":
        if align.get("unit") not in mc.UNITS:
            raise ValueError("기간 단위로 맞출 때는 day·week·month 중 하나를 골라 주세요.")
    elif align.get("kind") != "key" or not align.get("keys"):
        raise ValueError("맞춤 구절은 {kind: period, unit} 또는 {kind: key, keys: [[A 컬럼, B 컬럼]]} 모양이어야 해요.")
    return align


def ad_table(s, ctx, align):
    """광고비 테이블(없는 컬럼이면 이유와 함께 거부)과 맞춤에 쓰는 컬럼 이름들."""
    if ctx.other is None:
        raise ValueError("다른 테이블을 읽을 수 없는 곳이에요.")
    if align["kind"] == "period" and not s.get("ad_time"):
        raise ValueError("기간 단위로 맞출 때는 광고 날짜 컬럼을 골라 주세요.")
    ad = ctx.other(s["ad_table"])
    names = [s["spend"], *([s["ad_time"]] if align["kind"] == "period" else [*(k[1] for k in align["keys"]), *([s["ad_time"]] if s.get("ad_time") else [])])]
    mc.need(ad, *names)
    return ad


def spend_groups(ad, s, align, days=None):
    """광고비 합계 {맞춤 키: 광고비}. 공통 키 맞춤에서 days=(첫 날 번호, 끝 날 번호)를 주면 광고 날짜가 그 사이인 광고비만."""
    if align["kind"] == "period":
        x = ad.select(p=mc.bucket_index(ad[s["ad_time"]], align["unit"]), v=ad[s["spend"]])
        return {int(r["p"]): num(r["v"]) or 0.0 for r in mc.fetch_rows(x.group_by("p").aggregate(v=x.v.sum()), "광고비 기간 수") if r["p"] is not None}
    cols = [k[1] for k in align["keys"]]
    if days:
        d = mc.day_index(ad[s["ad_time"]])
        ad = ad.filter(d >= days[0], d <= days[1])
    x = ad.select(*[ad[c].cast("string").name(f"k{i}") for i, c in enumerate(cols)], v=ad[s["spend"]])
    return {tuple(r[f"k{i}"] for i in range(len(cols))): num(r["v"]) or 0.0 for r in mc.fetch_rows(x.group_by([f"k{i}" for i in range(len(cols))]).aggregate(v=x.v.sum()), "광고비 키 수")}


def cohort_frame(t, s, align, with_amount=False):
    """사용자 쪽 작업 표: 행마다 (u, day, 맞춤 키 컬럼들, amt). 유료 사용자만 보려면 유입 컬럼 == 유료 값인 행만 남긴다(paid_only)."""
    x = t
    if s.get("paid_only"):
        x = x.filter(x[s["source"]] == funnel.step_value(x[s["source"]].type(), s["paid_value"]))
    cols = {"u": x[s["user"]], "day": mc.day_index(x[s["time"]])}
    if align["kind"] == "period":
        cols["p"] = mc.bucket_index(x[s["time"]], align["unit"])
    else:
        cols.update({f"k{i}": x[k[0]].cast("string") for i, k in enumerate(align["keys"])})
    if with_amount:
        cols["amt"] = x[s["amount"]].fill_null(0)
    y = x.select(**cols)
    return y.filter(y.u.notnull(), y.day.notnull())


def key_names(align):
    """맞춤 키 컬럼 이름들."""
    return ["p0"] if align["kind"] == "period" else [f"k{i}" for i in range(len(align["keys"]))]


def first_keys(x, names):
    """공통 키 맞춤에서 사용자마다 첫 활동(같은 날이면 키 순)의 키 한 줄: (u, 키 컬럼들). 신규 수와 매출이 같은 키를 쓰게 한 곳에서 고른다(창 함수 row_number)."""
    ranked = x.mutate(_r=ibis.row_number().over(ibis.window(group_by="u", order_by=["day", *names])))
    return ranked.filter(ranked._r == 0).select("u", *names)


def new_users(x, align):
    """맞춤 키마다 신규 사용자 수. 기간 맞춤은 첫 활동 기간, 공통 키 맞춤은 사용자의 첫 활동(같은 날이면 키 순) 키 하나에만(한 사용자를 여러 키로 세지 않는다)."""
    if align["kind"] == "period":
        first = x.group_by("u").aggregate(p0=x.p.min())
        return {int(r["p0"]): int(r["n"]) for r in mc.fetch_rows(first.group_by("p0").aggregate(n=first.u.count()), "기간 수")}
    names = key_names(align)
    first = first_keys(x, names)
    return {tuple(r[k] for k in names): int(r["n"]) for r in mc.fetch_rows(first.group_by(names).aggregate(n=first.u.count()), "키 수")}


def revenue_by_offset(x, align, max_days):
    """{(맞춤 키, 첫 활동일로부터의 일수): 매출}. 사용자별 첫 활동일을 기준으로 max_days일 안만. 공통 키 맞춤은 매출도 사용자의 첫 키(new_users가 센 키)로 묶는다."""
    names = key_names(align)
    if align["kind"] == "period":
        first = x.group_by("u").aggregate(d0=x.day.min(), p0=x.p.min())
        j = x.join(first, "u")
    else:
        first = x.group_by("u").aggregate(d0=x.day.min())
        j = x.select("u", "day", "amt").join(first, "u").join(first_keys(x, names), "u")
    j = j.mutate(off=j.day - j.d0)
    j = j.filter(j.off <= max_days)
    got = mc.fetch_rows(j.group_by([*names, "off"]).aggregate(r=j.amt.sum()), "키·일수 조합")
    return {((int(r["p0"]) if align["kind"] == "period" else tuple(r[k] for k in names)), int(r["off"])): num(r["r"]) or 0.0 for r in got}


def key_text(key, align):
    """맞춤 키를 보일 글."""
    return label_of(key, align["unit"]) if align["kind"] == "period" else " / ".join(str(k) for k in key)


def cross_context(t, s, ctx, align, paid_only=False, max_days=0):
    """두 테이블 계산의 바탕: (키마다 광고비, 키마다 신규 사용자 수, x(사용자 쪽 표), align)."""
    align = check_align(align)
    ad = ad_table(s, ctx, align)
    x = cohort_frame(t, {**s, "paid_only": paid_only}, align, with_amount=max_days > 0 or "amount" in s)
    users = new_users(x, align)
    if align["kind"] == "period" or s.get("ad_time"):  # 광고비 테이블에는 기간이 안 걸려 있으니 사용자 쪽 활동 범위로 자른다(기간 맞춤은 기간 번호, 공통 키 맞춤은 날짜 번호)
        col = x.p if align["kind"] == "period" else x.day
        r = one(x.aggregate(lo=col.min(), hi=col.max()))
        days = (r["lo"], r["hi"]) if r["lo"] is not None else (1, 0)  # 사용자 쪽이 비면 빈 범위
        spend = spend_groups(ad, s, align, days)
        if align["kind"] == "period":
            spend = {k: v for k, v in spend.items() if days[0] <= k <= days[1]}
    else:
        spend = spend_groups(ad, s, align)
        ctx.notes.append("광고비에는 기간이 걸리지 않아요(광고 날짜 컬럼을 고르면 사용자 쪽 날짜 범위로 거를 수 있어요).")
    if align["kind"] == "key" and spend and users and not spend.keys() & users.keys():
        ctx.notes.append("사용자 쪽과 광고 쪽에서 맞는 키 값이 하나도 없어요. 두 컬럼의 키 타입을 확인하세요(예: 정수 1과 실수 1.0은 글자가 달라 맞지 않아요).")
    ctx.notes.append("고른 기간 앞의 활동은 보이지 않아, 기간 안에서 처음 나타난 사용자를 신규로 세요(기존 사용자가 섞일 수 있어요).")
    return spend, users, x, align


@kernel("cac")
def cac(t, s, p, ctx):
    """CAC(전체·유료): 광고비 ÷ 신규 사용자. 맞춤 구절이 있어야 한다."""
    spend, users, x, align = cross_context(t, s, ctx, ctx.align, paid_only=p["paid"])
    keys = sorted(spend, key=str)
    rows = [{"key": key_text(k, align), "spend": spend[k], "new_users": users.get(k, 0), "cac": mc.ratio(spend[k], users.get(k, 0))} for k in keys]
    total_spend, total_new = sum(spend.values()), sum(users.get(k, 0) for k in keys)
    ctx.notes.append("광고비가 있는 기간(키)의 신규 사용자만 나눠요." + (" 유입 경로가 유료인 사용자만 세요." if p["paid"] else " 오가닉 사용자도 모두 세요."))
    return result(mc.ratio(total_spend, total_new), last_rows(rows, ctx, "키"), {"key": "맞춘 키", "spend": "광고비", "new_users": "신규 사용자", "cac": "CAC"},
                  {"spend": total_spend, "new_users": total_new}, label="광고비 합계 ÷ 신규 사용자 수")


@kernel("roas")
def roas(t, s, p, ctx):
    """ROAS D_N·LTV:CAC: 첫 활동 후 N일 매출을 광고비(또는 CAC)와 견준다."""
    n_days = s["n_days"]
    spend, users, x, align = cross_context(t, s, ctx, ctx.align, max_days=n_days)
    by = revenue_by_offset(x, align, n_days)
    rev = {}
    for (k, _), v in by.items():
        rev[k] = rev.get(k, 0.0) + v
    keys = sorted(spend, key=str)
    rows = [{"key": key_text(k, align), "spend": spend[k], "revenue": rev.get(k, 0.0), "roas": mc.roas(rev.get(k, 0.0), spend[k])} for k in keys]
    total_spend, total_rev = sum(spend.values()), sum(rev.get(k, 0.0) for k in keys)
    ctx.notes.append("광고비가 있는 기간(키)의 신규 사용자·매출만 견줘요.")
    if p.get("ltv_cac"):
        new = sum(users.get(k, 0) for k in keys)
        ltv_n, cac_all = mc.ratio(total_rev, new), mc.ratio(total_spend, new)
        rows = [{"key": r["key"], "ltv": mc.ratio(r["revenue"], users.get(k, 0)), "cac": mc.ratio(r["spend"], users.get(k, 0)),
                 "ltv_cac": mc.ratio(mc.ratio(r["revenue"], users.get(k, 0)), mc.ratio(r["spend"], users.get(k, 0)))} for r, k in zip(rows, keys)]
        ctx.notes.append(f"자료의 끝에서 {n_days}일이 덜 지난 코호트는 매출이 덜 잡혀 LTV가 낮게 나올 수 있어요.")
        return result(mc.ratio(ltv_n, cac_all), last_rows(rows, ctx, "키"), {"key": "맞춘 키", "ltv": f"LTV{n_days}", "cac": "CAC", "ltv_cac": "LTV:CAC"},
                      {"ltv": ltv_n, "cac": cac_all}, label=f"LTV{n_days} ÷ CAC")
    ctx.notes.append(f"각 사용자의 첫 활동일로부터 {n_days}일 안의 매출 ÷ 광고비예요. 자료의 끝에서 {n_days}일이 덜 지난 코호트는 매출이 덜 잡혀 낮게 나올 수 있어요.")
    return result(mc.roas(total_rev, total_spend), last_rows(rows, ctx, "키"), {"key": "맞춘 키", "spend": "광고비", "revenue": f"{n_days}일 매출", "roas": f"ROAS D{n_days}"},
                  {"revenue_n": total_rev, "spend": total_spend}, label=f"D{n_days} 누적 매출 ÷ 광고비")


@kernel("payback")
def payback(t, s, p, ctx):
    """회수 기간: 누적 ARPU가 CAC 이상이 되는 첫 일수."""
    top = s["max_days"]
    spend, users, x, align = cross_context(t, s, ctx, ctx.align, max_days=top)
    by = revenue_by_offset(x, align, top)
    keys = set(spend)
    new = sum(users.get(k, 0) for k in keys)
    cac_all = mc.ratio(sum(spend.values()), new)
    daily = [0.0] * (top + 1)
    for (k, off), v in by.items():
        if k in keys:
            daily[off] += v
    cum, run = [], 0.0
    for v in daily:
        run += v
        cum.append(mc.ratio(run, new))
    day = None if cac_all is None else mc.payback_day(cum, cac_all)
    rows = [{"day": n, "cum_arpu": v, "cac": cac_all} for n, v in enumerate(cum)]
    if day is None:
        ctx.notes.append(f"{top}일 안에는 CAC를 회수하지 못했어요.")
    step = max(1, len(rows) // MAX_POINTS)
    if step > 1:
        ctx.notes.append(f"점이 많아 {step}일마다 하나씩만 표에 실었어요(대표 값은 전체 기준).")
    shown = rows[::step]
    if shown[-1] is not rows[-1]:
        shown.append(rows[-1])  # 솎아도 마지막 날은 싣는다
    return result(day, shown, {"day": "첫 활동 후 일수", "cum_arpu": "누적 ARPU", "cac": "CAC"}, {"cac": cac_all, "new_users": new}, label="누적 ARPU가 CAC 이상이 되는 첫 일수")


# ---------- 6 게임 경제 ----------

@kernel("flow")
def flow(t, s, p, ctx):
    """Faucet·Sink·Net Flow·Sink-to-Faucet: 재화 변화량의 +/− 합."""
    d = t[s["delta"]]
    r = one(t.aggregate(f=ibis.ifelse(d > 0, d, 0).sum(), k=ibis.ifelse(d < 0, -d, 0).sum()))
    faucet, sink = num(r["f"]) or 0.0, num(r["k"]) or 0.0
    parts = {"faucet": faucet, "sink": sink}
    pick = p["pick"]
    value = {"faucet": faucet, "sink": sink, "net": faucet - sink, "ratio": mc.ratio(sink, faucet)}[pick]
    return result(value, parts=parts, label={"faucet": "들어온 재화(+ 변화량의 합)", "sink": "나간 재화(− 변화량의 합)", "net": "Faucet − Sink", "ratio": "Sink ÷ Faucet"}[pick])


def money_supply(t, s, ctx):
    """활성 사용자의 마지막 잔액 합계 (합계, 활성 사용자 수). 활성 = 자료의 마지막 날 기준 최근 N일 안에 활동이 있는 사용자."""
    x = with_user(t, s)
    x = x.select(u=x[s["user"]], d=mc.day_index(x[s["time"]]), b=x[s["balance"]], tm=x[s["time"]])
    x = x.filter(x.d.notnull())
    known = x.filter(x.b.notnull())  # 마지막 행의 잔액이 비면 마지막으로 알려진 잔액. 같은 시각이 여럿이면 그중 큰 잔액
    at = known.group_by("u").aggregate(tm=known.tm.max())
    same = known.join(at, ["u", "tm"])
    bal = same.group_by("u").aggregate(bal=same.b.max())
    per = x.group_by("u").aggregate(last=x.d.max()).left_join(bal, "u")
    end = one(x.aggregate(m=x.d.max()))["m"]  # 사용자별 마지막 날의 최댓값 = 전체의 마지막 날(사용자별 집계 없이 한 번에)
    if end is None:
        return 0.0, 0
    live = per.filter(per["last"] > end - s["active_days"])
    r = one(live.aggregate(total=live.bal.sum(), n=live.count()))
    return num(r["total"]) or 0.0, int(r["n"])


@kernel("money_supply")
def money_supply_kernel(t, s, p, ctx):
    """활성 통화량."""
    total, n = money_supply(t, s, ctx)
    return result(total, parts={"active_money_supply": total, "active_users": n}, label=f"최근 {s['active_days']}일 안에 활동한 사용자의 마지막 잔액 합계")


@kernel("velocity")
def velocity(t, s, p, ctx):
    """화폐 유통 속도: Sink ÷ 활성 통화량."""
    d = t[s["delta"]]
    sink = num(one(t.aggregate(k=ibis.ifelse(d < 0, -d, 0).sum()))["k"]) or 0.0
    supply, n = money_supply(t, s, ctx)
    return result(mc.ratio(sink, supply), parts={"sink": sink, "active_money_supply": supply}, label="나간 재화 ÷ 활성 통화량")


@kernel("gini")
def gini(t, s, p, ctx):
    """지니 계수(사용자별 합계·마지막 값 또는 행 값)."""
    col = s["value"]
    if not s.get("user"):
        values = column_array(t, col, ctx)
    elif s["basis"] == "사용자별 마지막 값":
        if not s.get("time"):
            raise ValueError("‘사용자별 마지막 값’에는 시각 컬럼이 필요해요.")
        x = with_user(t, s)
        values = column_array(x.group_by(s["user"]).aggregate(v=x[col].argmax(x[s["time"]])), "v", ctx)
    else:
        x = with_user(t, s)
        values = column_array(x.group_by(s["user"]).aggregate(v=x[col].sum()), "v", ctx)
    g = mc.gini(values)
    if g is None:
        ctx.notes.append("값에 음수가 있거나 합이 0이면 지니 계수를 구할 수 없어요.")
    return result(g, parts={"holders": int(len(values))}, label="0 = 모두 같음, 1 = 한 사람이 모두 가짐")


@kernel("stage_clear")
def stage_clear(t, s, p, ctx):
    """스테이지 클리어율·도전 횟수."""
    t = t.filter(t[s["result"]].notnull(), t[s["stage"]].notnull())  # 결과·스테이지가 빈 행은 도전으로 세지 않는다
    if s.get("user"):
        t = with_user(t, s)
    win = t[s["result"]] == funnel.step_value(t[s["result"]].type(), s["win_value"])
    x = t.select(g=t[s["stage"]], u=t[s["user"]] if s.get("user") else ibis.literal(0), w=win.cast("int64"))
    per = x.group_by("g").aggregate(tries=x.count(), clears=x.w.sum(), users=x.u.nunique())
    found = sorted(mc.fetch_rows(per, "스테이지 수"), key=lambda r: (r["g"] != r["g"], r["g"]))  # 스테이지 값의 원래 타입 순(숫자는 숫자 순, NaN은 맨 뒤)
    rows = [{"stage": str(r["g"]), "tries": int(r["tries"]), "clears": int(r["clears"] or 0), "users": int(r["users"]), "clear_rate": mc.ratio(int(r["clears"] or 0), int(r["tries"])),
             "try_per_user": mc.ratio(int(r["tries"]), int(r["users"]))} for r in found]
    tries, clears = sum(r["tries"] for r in rows), sum(r["clears"] for r in rows)
    cols = {"stage": "스테이지", "tries": "도전 수", "clears": "클리어 수", "clear_rate": "클리어율", "users": "도전한 사용자", "try_per_user": "사용자 1명당 도전 수"}
    if p["pick"] == "clear":
        return result(mc.ratio(clears, tries), last_rows(rows, ctx, "스테이지"), cols, {"tries": tries, "clears": clears}, label="전체 클리어율")
    pair = x.group_by(["g", "u"]).aggregate(n=x.count())
    return result(mc.ratio(tries, int(one(pair.aggregate(n=pair.count()))["n"])), last_rows(rows, ctx, "스테이지"), cols, {"tries": tries}, dist=column_array(pair, "n", ctx),
                  label="(사용자, 스테이지) 1개당 도전 수")


@kernel("meta")
def meta(t, s, p, ctx):
    """항목별 Pick Rate·Win Rate."""
    t = t.filter(t[s["result"]].notnull(), t[s["item"]].notnull())  # 결과·항목이 빈 행은 선택으로 세지 않는다
    win = t[s["result"]] == funnel.step_value(t[s["result"]].type(), s["win_value"])
    x = t.select(g=t[s["item"]], w=win.cast("int64"))
    found = mc.fetch_rows(x.group_by("g").aggregate(picks=x.count(), wins=x.w.sum()), "항목 수")
    total = sum(int(r["picks"]) for r in found)
    found.sort(key=lambda r: (-int(r["picks"]), r["g"]))
    rows = [{"item": str(r["g"]), "picks": int(r["picks"]), "pick_rate": mc.ratio(int(r["picks"]), total), "wins": int(r["wins"] or 0), "win_rate": mc.ratio(int(r["wins"] or 0), int(r["picks"]))} for r in found]
    cols = {"item": "항목", "picks": "선택 수", "pick_rate": "Pick Rate", "wins": "승리 수", "win_rate": "Win Rate"}
    if not rows:
        return result(None)
    top = rows[0]
    wins = sum(r["wins"] for r in rows)
    value = top["pick_rate"] if p["pick"] == "pick" else mc.ratio(wins, total)
    return result(value, top_rows(rows, ctx, "항목"), cols, {"picks": total, "wins": wins}, label=f"가장 많이 고른 ‘{top['item']}’의 비율" if p["pick"] == "pick" else "전체 승률")


@kernel("pity")
def pity(t, s, p, ctx):
    """천장 도달률: 뽑기 N번 이상 한 사용자 ÷ 뽑은 사용자."""
    x = only_event(with_user(t, s), s)
    per = x.group_by(s["user"]).aggregate(n=x.count())
    r = one(per.aggregate(users=per.count(), reached=(per.n >= s["pity"]).cast("int64").sum()))
    users, reached = int(r["users"]), int(r["reached"] or 0)
    return result(mc.ratio(reached, users), parts={"pulled_users": users, "reached_users": reached}, dist=column_array(per, "n", ctx), label=f"뽑은 사용자 중 {s['pity']}번 이상 뽑은 비율")


# ---------- 7 이커머스 ----------

@kernel("gmv")
def gmv(t, s, p, ctx):
    """GMV: 금액 합(상태 값으로 취소 등 제외 가능)."""
    x = t
    if s.get("status") and s.get("exclude"):
        x = t.filter(~t[s["status"]].isin([funnel.step_value(t[s["status"]].type(), v) for v in s["exclude"]]).fill_null(False))
    n = num(one(x.aggregate(n=x[s["amount"]].sum()))["n"]) or 0.0
    return result(n, parts={"gmv": n}, label="거래액 합계")


@kernel("take_rate")
def take_rate(t, s, p, ctx):
    """수수료율: 수수료 수익 ÷ GMV."""
    r = one(t.aggregate(fee=t[s["fee_col"]].sum(), g=t[s["amount"]].sum()))
    fee, g = num(r["fee"]) or 0.0, num(r["g"]) or 0.0
    return result(mc.ratio(fee, g), parts={"fee_revenue": fee, "gmv": g}, label="수수료 수익 ÷ 거래액")


@kernel("aov")
def aov(t, s, p, ctx):
    """AOV: 주문 1건당 금액. 분포는 주문별 금액."""
    x = t.filter(t[s["amount"]].notnull())  # 금액이 빈 행은 주문 수에도 세지 않는다
    if s.get("orders"):
        per = x.filter(x[s["orders"]].notnull()).group_by(s["orders"]).aggregate(v=x[s["amount"]].sum())
    else:
        per = x.select(v=x[s["amount"]])
    r = one(per.aggregate(total=per.v.sum(), n=per.count()))
    total, n = num(r["total"]) or 0.0, int(r["n"])
    return result(mc.ratio(total, n), parts={"gmv": total, "orders": n}, dist=column_array(per, "v", ctx), label="주문 1건당 금액")


@kernel("purchase_frequency")
def purchase_frequency(t, s, p, ctx):
    """구매 빈도: 구매자 1명당 주문 수."""
    x = with_user(t, s)
    per = x.group_by(s["user"]).aggregate(n=x[s["orders"]].nunique() if s.get("orders") else x.count())
    r = one(per.aggregate(orders=per.n.sum(), buyers=per.count()))
    orders, buyers = int(r["orders"] or 0), int(r["buyers"])
    return result(mc.ratio(orders, buyers), parts={"orders": orders, "buyers": buyers}, dist=column_array(per, "n", ctx), label="구매자 1명당 주문 수")


@kernel("zero_result")
def zero_result(t, s, p, ctx):
    """검색 결과 0건 비율."""
    x = only_event(t.filter(t[s["results"]].notnull()), s)
    r = one(x.aggregate(n=x.count(), z=(x[s["results"]] == 0).cast("int64").sum()))
    n, zero = int(r["n"]), int(r["z"] or 0)
    return result(mc.ratio(zero, n), parts={"searches": n, "zero_searches": zero}, label="검색 중 결과가 0건인 비율")


@kernel("search_exit")
def search_exit(t, s, p, ctx):
    """검색 이탈률: 검색한 세션 중 검색이 마지막 행동인 비율."""
    ses, _ = sessions(t, s)
    kind = ses[s["event"]].type()
    value = funnel.step_value(kind, s["event_value"])
    per = ses.group_by("session").aggregate(last=ses[s["event"]].argmax(ses[s["time"]]), found=(ses[s["event"]] == value).cast("int64").sum())
    per = per.filter(per.found > 0)
    r = one(per.aggregate(n=per.count(), out=(per["last"] == value).cast("int64").sum()))
    n, out = int(r["n"]), int(r["out"] or 0)
    return result(mc.ratio(out, n), parts={"search_sessions": n, "exit_sessions": out}, label="검색한 세션 중 검색이 마지막 행동인 비율")


@kernel("pareto")
def pareto(t, s, p, ctx):
    """SKU Pareto: 상위 p% 항목의 매출 몫."""
    x = t.filter(t[s["item"]].notnull())
    per = x.group_by(s["item"]).aggregate(v=x[s["amount"]].sum())
    values = column_array(per, "v", ctx)
    share = mc.pareto_share(values, s["top"] / 100)
    return result(share, parts={"items": int(len(values))}, label=f"상위 {s['top']:g}% 항목이 차지하는 매출 비중")


# ---------- 8 실험·모델 ----------

def pairs(t, s, names, ctx):
    """역할 이름들의 컬럼을 numpy 배열 dict로(빈 값이 있는 행은 뺀다, 참거짓은 0/1). 상한+1행까지만 묻는다."""
    cols = {n: (t[s[n]].cast("int64") if t[s[n]].type().is_boolean() else t[s[n]]) for n in names}
    x = t.select(**cols)
    x = x.filter(*[x[n].notnull() for n in names]).limit(mc.MAX_FETCH_ROWS + 1)
    got = x.to_pyarrow()
    if got.num_rows > mc.MAX_FETCH_ROWS:
        got = got.slice(0, mc.MAX_FETCH_ROWS)
        ctx.notes.append(f"행이 {mc.MAX_FETCH_ROWS:,}개를 넘어 일부만 썼어요(표본 기준).")
    return {n: got[n].to_numpy(zero_copy_only=False) for n in names}


@kernel("classification")
def classification(t, s, p, ctx):
    """Precision·Recall·F1·PR-AUC."""
    names = ["y_true"] + [n for n in ("y_pred", "score") if s.get(n)]
    if len(names) == 1:
        raise ValueError("예측 컬럼이나 점수 컬럼이 필요해요.")
    a = pairs(t, s, names, ctx)
    if p["pick"] == "pr_auc" and "score" not in a:
        raise ValueError("PR-AUC에는 점수 컬럼이 필요해요.")
    got = mc.classification_metrics(a["y_true"], y_pred=a.get("y_pred"), score=a.get("score"), threshold=s.get("threshold", 0.5))
    rows = [{"name": k.upper(), "value": got[k]} for k in ("tp", "fp", "fn", "tn")] + [{"name": n, "value": got[k]} for n, k in (("Precision", "precision"), ("Recall", "recall"), ("F1", "f1"), ("PR-AUC", "pr_auc"))]
    return result(got[p["pick"]], rows, {"name": "항목", "value": "값"}, {k: got[k] for k in ("tp", "fp", "fn")}, label={"precision": "TP ÷ (TP + FP)", "recall": "TP ÷ (TP + FN)", "f1": "정밀도와 재현율의 조화평균", "pr_auc": "평균 정밀도(점수 순)"}[p["pick"]])


@kernel("regression")
def regression(t, s, p, ctx):
    """RMSE·MAE·MAPE."""
    a = pairs(t, s, ["y_true", "y_pred"], ctx)
    got = mc.regression_metrics(a["y_true"], a["y_pred"])
    if p["pick"] == "mape" and got["excluded"]:
        ctx.notes.append(f"정답이 0인 {got['excluded']}행은 MAPE에서 뺐어요.")
    return result(got[p["pick"]], [{"name": n, "value": got[k]} for n, k in (("RMSE", "rmse"), ("MAE", "mae"), ("MAPE", "mape"))], {"name": "항목", "value": "값"}, {"n": got["n"]},
                  label={"rmse": "√평균(오차²)", "mae": "평균 |오차|", "mape": "평균 |오차 ÷ 정답|"}[p["pick"]])


@kernel("ranking")
def ranking(t, s, p, ctx):
    """NDCG@K·MAP@K: 질의마다 구해 평균."""
    k = s["k"]
    a = pairs(t, s, ["qid", "rank", "rel"], ctx)
    groups = {}
    for q, r, rel in zip(a["qid"], a["rank"], a["rel"]):
        groups.setdefault(q, []).append((r, rel))
    scores = []
    for items in groups.values():
        items.sort(key=lambda x: x[0])
        rels = [x[1] for x in items]
        if p["pick"] == "ndcg":
            scores.append(mc.ndcg_at_k(rels, k, all_relevances=rels))
        else:
            hits = [1 if r > 0 else 0 for r in rels]
            scores.append(mc.ap_at_k(hits, k, total_relevant=sum(hits)))
    skipped = sum(1 for x in scores if x is None)
    if skipped:
        ctx.notes.append(f"관련 항목이 하나도 없는 질의 {skipped}개는 평균에서 뺐어요.")
    return result(mc.mean_defined(scores), parts={"queries": len(groups)}, label=f"질의별 {'NDCG' if p['pick'] == 'ndcg' else 'AP'}@{k}의 평균")


def two_groups(t, s):
    """실험군·대조군의 요약 {대조: {n, mean, sd, sum}, 실험: …}와 비율 지표인지."""
    kind = t[s["group"]].type()
    c, e = funnel.step_value(kind, s["control"]), funnel.step_value(kind, s["treat"])
    m = t[s["metric"]]
    x = t.filter(t[s["group"]].isin([c, e]), m.notnull())
    isbool = m.type().is_boolean()
    mm = m.cast("int64") if isbool else m
    r = mc.rows(x.group_by(s["group"]).aggregate(n=mm.count(), mean=mm.mean(), sd=mm.std(how="sample"), total=mm.sum(), lo=mm.min(), hi=mm.max()))
    found = {}
    for row in r:
        key = "c" if row[s["group"]] == c else "e"
        found[key] = {"n": int(row["n"]), "mean": num(row["mean"]), "sd": num(row["sd"]), "sum": num(row["total"]), "lo": num(row["lo"]), "hi": num(row["hi"])}
    if set(found) != {"c", "e"}:
        raise ValueError("대조군과 실험군 값이 모두 든 행이 있어야 해요. 두 군 이름을 확인해 주세요.")
    binary = (isbool or m.type().is_integer()) and all(f["lo"] >= 0 and f["hi"] <= 1 for f in found.values())
    return found["c"], found["e"], binary


@kernel("lift")
def lift_kernel(t, s, p, ctx):
    """Lift: (실험군 − 대조군) ÷ 대조군."""
    c, e, binary = two_groups(t, s)
    return result(mc.lift(e["mean"], c["mean"]), parts={"control_mean": c["mean"], "treat_mean": e["mean"], "control_n": c["n"], "treat_n": e["n"]}, label="(실험군 − 대조군) ÷ 대조군")


@kernel("pvalue")
def pvalue(t, s, p, ctx):
    """p-value: 비율이면 두 비율 z 검정, 연속이면 Welch t 검정."""
    c, e, binary = two_groups(t, s)
    if binary:
        got = mc.two_proportion_z(round(c["sum"]), c["n"], round(e["sum"]), e["n"])
        test, value = "두 비율 z 검정", got["p"]
    else:
        got = mc.welch_from_stats(c["mean"], c["sd"] or 0.0, c["n"], e["mean"], e["sd"] or 0.0, e["n"])
        test, value = "Welch t 검정", got["p"]
    verdict = "차이가 우연이라고 보기 어려워요" if value is not None and value < s["alpha"] else "차이가 우연일 수 있어요"
    rows = [{"name": "대조군 평균", "value": c["mean"]}, {"name": "실험군 평균", "value": e["mean"]}, {"name": "차이", "value": e["mean"] - c["mean"]}, {"name": "p-value", "value": value}]
    ctx.notes.append(f"{test}, 양측, 유의수준 {s['alpha']:g}: {verdict}.")
    return result(value, rows, {"name": "항목", "value": "값"}, {"control_n": c["n"], "treat_n": e["n"]}, label=test)


@kernel("power")
def power(t, s, p, ctx):
    """검정력(정규 근사)."""
    c, e, binary = two_groups(t, s)
    if binary:
        pw = mc.power_two_proportions(c["mean"], e["mean"], c["n"], e["n"], s["alpha"])
        delta = e["mean"] - c["mean"]
        if pw is None:
            ctx.notes.append("두 군의 비율이 모두 0 또는 1이라 표준오차가 0이어서 검정력을 구할 수 없어요.")
    elif c["sd"] is None or e["sd"] is None:
        pw, delta = None, e["mean"] - c["mean"]
        ctx.notes.append("한 군에 값이 1개뿐이라 표준편차를 구할 수 없어 검정력을 구할 수 없어요.")
    else:
        sd = math.sqrt((c["sd"] ** 2 + e["sd"] ** 2) / 2)
        if sd == 0:
            pw = None
            ctx.notes.append("각 군 안의 값이 모두 같아 표준편차가 0이라 검정력을 구할 수 없어요.")
        else:
            pw = mc.power_two_means(e["mean"] - c["mean"], sd, c["n"], e["n"], s["alpha"])
        delta = e["mean"] - c["mean"]
    return result(pw, parts={"control_n": c["n"], "treat_n": e["n"]}, label=f"지금 보인 차이({delta:.4g})를 유의수준 {s['alpha']:g}에서 잡아낼 확률(정규 근사)")


@kernel("mde")
def mde(t, s, p, ctx):
    """최소 검출 효과 MDE(정규 근사)."""
    c, e, binary = two_groups(t, s)
    if binary:
        value = mc.mde_two_proportions(c["mean"], c["n"], e["n"], s["alpha"], s["power"])
        if value is None:
            ctx.notes.append("대조군 비율이 0 또는 1이라 분산이 0이어서 최소 검출 효과를 구할 수 없어요.")
    else:
        if c["sd"] is None or e["sd"] is None:
            value = None
            ctx.notes.append("한 군에 값이 1개뿐이라 표준편차를 구할 수 없어 최소 검출 효과를 구할 수 없어요.")
        elif (sd := math.sqrt((c["sd"] ** 2 + e["sd"] ** 2) / 2)) == 0:
            value = None
            ctx.notes.append("각 군 안의 값이 모두 같아 표준편차가 0이라 최소 검출 효과를 구할 수 없어요.")
        else:
            value = mc.mde_two_means(sd, c["n"], e["n"], s["alpha"], s["power"])
    return result(value, [{"name": "MDE(절대)", "value": value}, {"name": "대조군 평균", "value": c["mean"]}, {"name": "MDE(대조군 대비)", "value": None if value is None else mc.ratio(value, c["mean"])}],
                  {"name": "항목", "value": "값"}, {"control_n": c["n"], "treat_n": e["n"]}, label=f"검정력 {s['power']:g}, 유의수준 {s['alpha']:g}에서 잡아낼 수 있는 최소 차이(정규 근사)")


@kernel("guardrail")
def guardrail(t, s, p, ctx):
    """가드레일: 실험군의 Lift가 한계를 지키는지."""
    c, e, binary = two_groups(t, s)
    value = mc.lift(e["mean"], c["mean"])
    if value is None:
        return result(None, label="대조군 값이 0이라 비교할 수 없어요")
    bad = value < s["limit"] / 100 if s["op"] == ">=" else value > s["limit"] / 100
    text = f"실험군이 대조군보다 {value * 100:+.2f}%예요. 한계({s['limit']:g}% {'이상' if s['op'] == '>=' else '이하'})를 {'벗어났어요' if bad else '지키고 있어요'}."
    return result(value, [{"name": "Lift", "value": value}, {"name": "한계(%)", "value": s["limit"]}, {"name": "상태", "value": "경고" if bad else "정상"}], {"name": "항목", "value": "값"},
                  {"control_mean": c["mean"], "treat_mean": e["mean"]}, label=text, notes=[text] if bad else [])
