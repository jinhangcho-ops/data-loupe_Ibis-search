"""지표 계산 바탕: 지표 사전(metric_catalog)이 조합해 쓰는 작은 계산들. 행 단위·그룹 단위 일은 Ibis 식으로(어느 연결에서나 같게), 배열 계산은 numpy로 한다.
- 세션·동시 수·사건 사이 시간·사용자 상태·코호트·LTV·NRR: 표(Ibis 식)를 받고, 큰 표는 Ibis가 집계해 작은 결과만 PC로 가져온다. 창 함수를 쓰는 것은 세션뿐이다(Polars 연결에서는 안 된다).
- 분포·생존(Kaplan-Meier)·예측 평가·실험 통계: 숫자 배열을 받는다(열 하나를 가져오는 fetch_column 포함). 실험 통계는 math·numpy만 쓴다.
- 시각은 초 단위로 센다(epoch_seconds). 기간 버킷 번호는 day/week(월요일 시작)/month 또는 N일 묶음이다.
- 구할 수 없는 값(0으로 나누기 등)은 None이다."""
import datetime as dt
import math

import numpy as np
from lazy import ibis

from funnel import step_value
from parse import check_name

MAX_FETCH_ROWS = 5_000_000  # 열 하나를 PC로 가져올 상한. 넘으면 표본이라고 알린다
EPOCH = dt.date(1970, 1, 1)
UNITS = ("day", "week", "month")
FLAT_EPS = 0.005  # 수평 유지점: 이어진 두 N의 차이가 이보다 작아야 한다(0.5%p)
FLAT_RUN = 3  # 수평 유지점: 이만큼 이어져야 한다
Q = (0.5, 0.9, 0.99)  # quantiles 기본
MONTH = np.timedelta64(1, "M")


# ---------- 공통 ----------

def need(table, *names):
    """표에 컬럼이 있는지 본다(없으면 비슷한 이름을 알린다)."""
    for name in names:
        if not isinstance(name, str) or not name:
            raise ValueError("컬럼을 골라 주세요.")
        check_name(name, table.columns)


def rows(expr):
    """식의 결과를 [{컬럼: 값}]으로."""
    return expr.to_pyarrow().to_pylist()


def epoch(col):
    """시각 컬럼의 초(정수)."""
    return col.cast("timestamp").epoch_seconds()


def day_index(col):
    """1970-01-01부터 센 날 번호."""
    return epoch(col) // 86400


def check_unit(unit):
    """묶음 단위: day·week·month 또는 N일(1 이상 정수)."""
    if unit in UNITS or (isinstance(unit, int) and not isinstance(unit, bool) and unit >= 1):
        return unit
    raise ValueError("기간 묶음은 day·week·month 중 하나이거나 1 이상의 일수여야 합니다.")


def bucket_index(col, unit):
    """시각이 든 기간 버킷의 번호(정수 식). week는 월요일 시작."""
    unit = check_unit(unit)
    if unit == "month":
        stamp = col.cast("timestamp")
        return stamp.year() * 12 + stamp.month() - 1
    days = day_index(col)
    if unit == "week":
        return (days + 3) // 7
    return days if unit == "day" else days // unit


def bucket_start(index, unit):
    """버킷 번호의 첫 날(date)."""
    unit = check_unit(unit)
    if unit == "month":
        return dt.date(index // 12, index % 12 + 1, 1)
    if unit == "week":
        return EPOCH + dt.timedelta(days=index * 7 - 3)
    return EPOCH + dt.timedelta(days=index * (1 if unit == "day" else unit))


def ratio(a, b):
    """a / b, b가 0이거나 없으면 None."""
    return a / b if b else None


# ---------- 세션 ----------

def add_sessions(table, user, time, gap_minutes=None, key=None):
    """표에 session(글자) 컬럼을 더한다. key(컬럼 이름 목록)를 주면 그 조합이 세션이고, 아니면 사용자별로 시각 순에서 앞 행과 gap_minutes분 넘게 벌어질 때마다 새 세션이다."""
    need(table, user, time)
    if key:
        need(table, *key)
        parts = [table[k].cast("string").fill_null("") for k in key]
        session = parts[0]
        for part in parts[1:]:
            session = session + ibis.literal("|") + part
        return table.mutate(session=session)
    if isinstance(gap_minutes, bool) or not isinstance(gap_minutes, (int, float)) or not gap_minutes > 0:
        raise ValueError("세션을 나눌 간격(분)은 0보다 큰 숫자여야 합니다.")
    t = table.mutate(_sec=epoch(table[time]))
    ordered = ibis.window(group_by=user, order_by=time)
    t = t.mutate(_new=((t._sec - t._sec.lag().over(ordered)) > gap_minutes * 60).fill_null(True).cast("int64"))
    t = t.mutate(_n=t._new.sum().over(ibis.cumulative_window(group_by=user, order_by=time)))
    return t.mutate(session=t[user].cast("string") + ibis.literal("#") + t._n.cast("string")).drop("_sec", "_new", "_n")


def session_stats(table, session, time, only=None):
    """세션 요약 {sessions, mean_length, median_length(초), bounces, bounce_rate}. 길이 = 마지막 − 처음 시각.
    bounce = 이벤트가 하나뿐인 세션. only(표 → 참거짓 식, 예: 페이지 이벤트)를 주면 그 이벤트만 세고, 그 이벤트가 있는 세션만 분모로 쓴다."""
    need(table, session, time)
    aggs = {"first": table[time].min(), "last": table[time].max(), "events": table.count()}
    if only is not None:
        aggs["events"] = only(table).cast("int64").sum()
    per = table.group_by(session).aggregate(**aggs)
    per = per.mutate(length=epoch(per["last"]) - epoch(per["first"]))
    if only is not None:
        per = per.filter(per.events > 0)
    one = per.aggregate(sessions=per.count(), mean=per.length.mean(), median=per.length.median(), bounces=(per.events == 1).cast("int64").sum())
    r = rows(one)[0]
    n, bounces = int(r["sessions"]), int(r["bounces"] or 0)
    return {"sessions": n, "mean_length": r["mean"], "median_length": r["median"], "bounces": bounces, "bounce_rate": ratio(bounces, n)}


def exit_rates(table, session, time, page):
    """페이지별 이탈률 [{page, views, exits, exit_rate}](조회 많은 순). exit = 세션의 마지막 페이지가 그 페이지인 세션 수, exit_rate = exits / views."""
    need(table, session, time, page)
    t = table.filter(table[page].notnull())
    last = t.group_by(session).aggregate(pg=t[page].argmax(t[time]))
    exits = last.group_by("pg").aggregate(exits=last.pg.count())
    views = t.group_by(page).aggregate(views=t.count()).rename(pg=page)
    found = {r["pg"]: r["exits"] for r in rows(exits)}
    out = [{"page": r["pg"], "views": int(r["views"]), "exits": int(found.get(r["pg"], 0))} for r in rows(views)]
    out.sort(key=lambda r: (-r["views"], str(r["page"])))
    return [{**r, "exit_rate": ratio(r["exits"], r["views"])} for r in out]


# ---------- 동시 수 ----------

def concurrency_series(table, start, end):
    """시작·끝 컬럼을 가진 구간 표에서 (변하는 시각 배열 datetime64, 그 시각부터의 동시 수 배열)을 구한다. 끝이 비면 아직 끝나지 않은 구간이고, 끝이 시작보다 앞선 행은 뺀다.
    같은 시각의 끝(−1)과 시작(+1)은 합쳐 세므로 끝난 자리에 바로 시작해도 겹치지 않는다."""
    need(table, start, end)
    s = table.filter(table[start].notnull(), table[end].isnull() | (table[end] >= table[start]))
    opens = s.select(t=s[start].cast("timestamp"), d=ibis.literal(1))
    closes = s.filter(s[end].notnull()).select(t=s[end].cast("timestamp"), d=ibis.literal(-1))
    events = opens.union(closes)
    steps = rows(events.group_by("t").aggregate(d=events.d.sum()).order_by("t"))
    times = np.array([r["t"] for r in steps], dtype="datetime64[us]")
    return times, np.cumsum([r["d"] for r in steps]).astype(np.int64)


def concurrency_stats(times, counts, start=None, end=None):
    """동시 수 요약 {pcu, acu}. PCU = 기간 안의 최대, ACU = Σ 동시 수 × 유지 시간 / 기간 길이. 기간(start, end)을 안 주면 첫 변화부터 마지막 변화까지.
    첫 변화 전의 동시 수는 0이고, 각 동시 수는 다음 변화 시각까지(마지막은 기간 끝까지) 이어진다."""
    times = np.asarray(times, dtype="datetime64[us]")
    if len(times) == 0:
        return {"pcu": 0, "acu": 0.0}
    lo = times[0] if start is None else np.datetime64(start, "us")
    hi = times[-1] if end is None else np.datetime64(end, "us")
    if hi < lo:
        raise ValueError("기간의 끝이 시작보다 앞입니다.")
    secs = (times - lo) / np.timedelta64(1, "s")  # 변화 시각(기간 시작 기준 초)
    total = float((hi - lo) / np.timedelta64(1, "s"))
    levels = np.concatenate(([0.0], np.asarray(counts, dtype=float)))
    edges = np.clip(np.concatenate(([0.0], secs, [total])), 0, total)  # 구간 i = [edges[i], edges[i+1]), 동시 수 levels[i]
    lengths = np.diff(edges)
    if total == 0:
        level = float(levels[np.searchsorted(secs, 0, side="right")])  # 그 순간에 이미 적용된 변화까지
        return {"pcu": int(level), "acu": level}
    return {"pcu": int(levels[lengths > 0].max()), "acu": float((levels * lengths).sum() / total)}


def monthly_peak(times, counts):
    """달별 최대 동시 수(MCU) {"YYYY-MM": 최대}. 앞 달에서 이어진 동시 수도 센다."""
    times = np.asarray(times, dtype="datetime64[us]")
    if len(times) == 0:
        return {}
    months = np.arange(times[0].astype("datetime64[M]"), times[-1].astype("datetime64[M]") + MONTH)
    return {str(m): concurrency_stats(times, counts, m.astype("datetime64[us]"), (m + MONTH).astype("datetime64[us]"))["pcu"] for m in months}


# ---------- 사건 사이 시간 ----------

def seconds_between(table, user, time, event, first, second):
    """사용자별로 first 사건의 가장 이른 시각과, 그 뒤(같은 시각 포함) second 사건의 가장 이른 시각의 차이(초) 배열, first를 한 사용자 수."""
    need(table, user, event, time)
    kind = table[event].type()
    a, b = step_value(kind, first), step_value(kind, second)
    base = table.select(u=table[user], e=table[event], t=table[time].cast("timestamp"))
    base = base.filter(base.u.notnull(), base.t.notnull())
    starts = base.filter(base.e == a).group_by("u").aggregate(ta=base.t.min())
    joined = base.filter(base.e == b).select("u", "t").join(starts, "u").filter(lambda j: j.t >= j.ta)
    done = joined.group_by("u").aggregate(ta=joined.ta.min(), tb=joined.t.min())
    gaps = done.select(sec=epoch(done.tb) - epoch(done.ta))
    values = np.array([r["sec"] for r in rows(gaps)], dtype=float)
    return values, int(rows(starts.aggregate(n=starts.u.count()))[0]["n"])


def summarize_seconds(values):
    """초 배열의 요약 {n, mean, median, p90}. 비면 값은 None."""
    v = np.asarray(values, dtype=float)
    if len(v) == 0:
        return {"n": 0, "mean": None, "median": None, "p90": None}
    return {"n": int(len(v)), "mean": float(v.mean()), "median": float(np.median(v)), "p90": float(np.quantile(v, 0.9))}


def time_between(table, user, time, event, first, second):
    """TTV·TTFP 같은 사건 사이 시간 {users_first, n, mean, median, p90}(초)."""
    values, users_first = seconds_between(table, user, time, event, first, second)
    return {"users_first": users_first, **summarize_seconds(values)}


def payback_day(cum_arpu, cac):
    """누적 ARPU(Day-0부터 하루씩)가 CAC 이상이 되는 첫 Day-N. 끝까지 못 넘으면 None."""
    for n, value in enumerate(cum_arpu):
        if value is not None and value >= cac:
            return n
    return None


# ---------- 사용자 상태 ----------

def active_buckets(table, user, time, unit):
    """(사용자 u, 활동한 기간 버킷 번호 p)의 중복 없는 표."""
    need(table, user, time)
    t = table.select(u=table[user], p=bucket_index(table[time], unit))
    return t.filter(t.u.notnull(), t.p.notnull()).distinct()


def state_counts(act):
    """기간 버킷별 활동자 수, 새로 나타난 수, 앞 기간에도 활동한 수를 {번호: 수} 셋으로."""
    first = act.group_by("u").aggregate(p=act.p.min())
    kept = act.join(act.select("u", p=act.p + 1), ["u", "p"])
    exprs = (act.group_by("p").aggregate(n=act.u.count()), first.group_by("p").aggregate(n=first.u.count()), kept.group_by("p").aggregate(n=kept.u.count()))
    return [{int(r["p"]): int(r["n"]) for r in rows(e)} for e in exprs]


def gap_counts(table, user, time, unit):
    """미접속 기준(inactive_days)용 집계. 사용자·기간 버킷마다 첫·마지막 날을 구해 (돌아온 행 {버킷: {쉰 일수: 수}}, 다음 기간에 활동이 없는 행 {버킷: {마지막 활동일: 수}}, 모든 행 [(버킷, 다음 활동 버킷, 마지막 활동일, 수)])를 돌려준다. 창 함수(lag·lead)를 쓴다."""
    need(table, user, time)
    t = table.select(u=table[user], d=day_index(table[time]), p=bucket_index(table[time], unit))
    t = t.filter(t.u.notnull(), t.d.notnull())
    b = t.group_by(["u", "p"]).aggregate(dmin=t.d.min(), dmax=t.d.max())
    w = ibis.window(group_by="u", order_by="p")
    b = b.mutate(pp=b.p.lag().over(w), pd=b.dmax.lag().over(w), nq=b.p.lead().over(w))
    back = b.filter(b.pp.notnull(), b.p - b.pp > 1)
    back = back.select("p", "u", gap=back.dmin - back.pd)
    returns = {}
    for r in rows(back.group_by(["p", "gap"]).aggregate(n=back.u.count())):
        returns.setdefault(int(r["p"]), {})[int(r["gap"])] = int(r["n"])
    last, rests = {}, []
    for r in rows(b.group_by(["p", "nq", "dmax"]).aggregate(n=b.u.count())):
        q, nq, day, n = int(r["p"]), (None if r["nq"] is None else int(r["nq"])), int(r["dmax"]), int(r["n"])
        rests.append((q, nq, day, n))
        if nq is None or nq - q > 1:  # 다음 기간에 활동이 없다
            last.setdefault(q, {})[day] = last.get(q, {}).get(day, 0) + n
    return returns, last, rests


def user_states(table, user, time, unit="month", inactive_days=None):
    """기간 버킷마다 사용자 상태 [{period(첫 날), active, new, retained, resurrected, churned, quick_ratio, churn_rate, resurrection_rate}].
    기본(inactive_days 없음)은 버킷만 본다: new = 첫 활동이 그 기간, retained = 앞 기간에도 활동, resurrected = 앞 기간에는 없고 그 전에는 활동한 적 있음, churned = 앞 기간에 활동했고 이번엔 없음.
    inactive_days = N(미접속 기준 일수)을 주면: churned = 앞 기간에 활동했고 이번엔 없으며 마지막 활동이 이 기간 끝보다 N일 이상 앞인 사람,
    resurrected = 이번에 활동했고 앞 기간엔 없으며(그 전 활동이 있고) 직전 활동과 N일 이상 벌어진 사람. 쉬었지만 N일이 안 된 사람이 돌아오면 retained로 센다.
    quick_ratio = (new + resurrected) / churned, churn_rate = churned / 앞 기간 활동자, resurrection_rate = resurrected / 기간 시작 때 휴면자(앞 기간까지 처음 나타난 사람 − 앞 기간 활동자. N을 주면 기간 시작보다 마지막 활동이 N일 이상 앞선 사람).
    unit에 일수 N을 주면 N일 묶음마다 본다. 첫 기간은 앞 기간이 없어 churned 등이 None이다."""
    check_unit(unit)
    if inactive_days is not None and (isinstance(inactive_days, bool) or not isinstance(inactive_days, int) or inactive_days < 1):
        raise ValueError("미접속 기준은 1 이상의 일수여야 합니다.")
    active, new, kept = state_counts(active_buckets(table, user, time, unit))
    if not active:
        return []
    returns, last, rests = gap_counts(table, user, time, unit) if inactive_days else ({}, {}, [])
    out, seen = [], 0  # seen = 앞 기간까지 처음 나타난 사람 수
    for p in range(min(active), max(active) + 1):
        a, n, k = active.get(p, 0), new.get(p, 0), kept.get(p, 0)
        retained, resurrected = k, a - n - k
        if inactive_days:
            back = returns.get(p, {})
            resurrected = sum(c for gap, c in back.items() if gap >= inactive_days)
            retained = k + sum(back.values()) - resurrected
        row = {"period": bucket_start(p, unit).isoformat(), "active": a, "new": n, "retained": retained, "resurrected": resurrected,
               "churned": None, "quick_ratio": None, "churn_rate": None, "resurrection_rate": None}
        if p > min(active):
            prev = active.get(p - 1, 0)
            row["churned"] = prev - k
            if inactive_days:
                end_day = (bucket_start(p + 1, unit) - EPOCH).days - 1
                row["churned"] = sum(c for day, c in last.get(p - 1, {}).items() if end_day - day >= inactive_days)
            row["quick_ratio"] = ratio(n + resurrected, row["churned"])
            row["churn_rate"] = ratio(row["churned"], prev)
            dormant = seen - prev
            if inactive_days:
                start_day = (bucket_start(p, unit) - EPOCH).days
                dormant = sum(c for q, nq, day, c in rests if q < p and (nq is None or p <= nq) and start_day - day >= inactive_days)
            row["resurrection_rate"] = ratio(resurrected, dormant)
        out.append(row)
        seen += n
    return out


# ---------- 코호트 ----------

def cohort_matrix(table, user, time, unit="day", mode="classic", n_max=30, span=None):
    """코호트(첫 활동 기간) × 경과 기간 N의 유지율 {unit, mode, cohorts: [{cohort, size, values}]}. 아직 관찰할 수 없는 칸은 None.
    classic = 정확히 N에 활동한 비율, rolling = N 이후에라도 활동한(마지막 활동 ≥ N) 비율, range = span=(a, b) 안에 활동한 비율(값이 하나)."""
    check_unit(unit)
    if mode not in ("classic", "rolling", "range"):
        raise ValueError("코호트 방식은 classic·rolling·range 중 하나입니다.")
    if mode == "range" and not (isinstance(span, (tuple, list)) and len(span) == 2 and 0 <= span[0] <= span[1]):
        raise ValueError("range 방식에는 span=(시작 N, 끝 N)이 있어야 합니다(0 ≤ 시작 ≤ 끝).")
    act = active_buckets(table, user, time, unit)
    first = act.group_by("u").aggregate(f=act.p.min())
    j = act.join(first, "u")
    j = j.mutate(off=j.p - j.f)
    sizes = {int(r["f"]): int(r["n"]) for r in rows(first.group_by("f").aggregate(n=first.u.count()))}
    last = int(rows(act.aggregate(m=act.p.max()))[0]["m"])
    counts = cohort_counts(j, mode, span)
    width = 1 if mode == "range" else n_max + 1
    cohorts = []
    for f in sorted(sizes):
        values = []
        for n in range(width):
            at = f + (span[0] if mode == "range" else n)
            values.append(ratio(counts.get((f, n), 0), sizes[f]) if at <= last else None)
        cohorts.append({"cohort": bucket_start(f, unit).isoformat(), "size": sizes[f], "values": values})
    return {"unit": unit, "mode": mode, "cohorts": cohorts}


def cohort_counts(j, mode, span):
    """{(코호트 번호 f, N): 사용자 수}. j는 활동(u, p, f, off) 표."""
    if mode == "classic":
        g = j.group_by(["f", "off"]).aggregate(n=j.u.count())
        return {(int(r["f"]), int(r["off"])): int(r["n"]) for r in rows(g)}
    if mode == "range":
        hit = j.filter(j.off >= span[0], j.off <= span[1])
        g = hit.group_by("f").aggregate(n=hit.u.nunique())
        return {(int(r["f"]), 0): int(r["n"]) for r in rows(g)}
    per_user = j.group_by(["u", "f"]).aggregate(m=j.off.max())
    g = per_user.group_by(["f", "m"]).aggregate(n=per_user.u.count())
    by_cohort = {}
    for r in rows(g):
        by_cohort.setdefault(int(r["f"]), {})[int(r["m"])] = int(r["n"])
    out = {}
    for f, found in by_cohort.items():
        running = 0
        for n in range(max(found), -1, -1):  # 마지막 활동이 N 이상인 사람 = 큰 쪽부터 누적
            running += found.get(n, 0)
            out[(f, n)] = running
    return out


def average_curve(cohorts):
    """코호트 크기로 가중한 평균 유지율 곡선(N마다, 값 있는 코호트만). 값이 하나도 없는 N은 None."""
    width = max((len(c["values"]) for c in cohorts), default=0)
    curve = []
    for n in range(width):
        have = [c for c in cohorts if c["values"][n] is not None]
        size = sum(c["size"] for c in have)
        curve.append(sum(c["values"][n] * c["size"] for c in have) / size if size else None)
    return curve


def flattening_point(curve, eps=FLAT_EPS, run=FLAT_RUN):
    """수평 유지점: |r(N) − r(N−1)| < eps가 run번 이어지는 첫 N(이어진 구간의 시작). 없으면 None."""
    streak = 0
    for n in range(1, len(curve)):
        if curve[n] is None or curve[n - 1] is None:
            streak = 0
            continue
        streak = streak + 1 if abs(curve[n] - curve[n - 1]) < eps else 0
        if streak >= run:
            return n - run + 1
    return None


def ltv(table, user, time, amount, n_days, unit="day"):
    """코호트(첫 활동 기간)별 LTV_N [{cohort, size, revenue, ltv, complete}]. revenue = 각 사용자의 첫 활동일로부터 N일 이내 매출 합, ltv = revenue / 코호트 크기.
    complete = 코호트의 마지막 날 + N일이 자료의 마지막 날 안이면 참(아니면 아직 N일이 다 안 지난 사람이 있다)."""
    need(table, user, time, amount)
    if isinstance(n_days, bool) or not isinstance(n_days, int) or n_days < 0:
        raise ValueError("N일은 0 이상의 정수여야 합니다.")
    check_unit(unit)
    t = table.select(u=table[user], day=day_index(table[time]), p=bucket_index(table[time], unit), amt=table[amount].fill_null(0))
    t = t.filter(t.u.notnull(), t.day.notnull())
    first = t.group_by("u").aggregate(d0=t.day.min(), f=t.p.min())
    j = t.join(first, "u")
    paid = j.filter(j.day - j.d0 <= n_days)
    revenue = {int(r["f"]): r["rev"] for r in rows(paid.group_by("f").aggregate(rev=paid.amt.sum()))}
    sizes = {int(r["f"]): int(r["n"]) for r in rows(first.group_by("f").aggregate(n=first.u.count()))}
    last_day = int(rows(t.aggregate(m=t.day.max()))[0]["m"])
    out = []
    for f in sorted(sizes):
        end_day = (bucket_start(f + 1, unit) - EPOCH).days - 1
        total = float(revenue.get(f, 0))
        out.append({"cohort": bucket_start(f, unit).isoformat(), "size": sizes[f], "revenue": total, "ltv": total / sizes[f], "complete": end_day + n_days <= last_day})
    return out


def roas(revenue, spend):
    """ROAS = 누적 매출 / 광고비(광고비가 0이면 None). 광고비와 매출의 키·날짜를 맞추는 것은 부르는 쪽 몫이다."""
    return ratio(revenue, spend)


def nrr(table, user, time, amount):
    """달별 NRR [{month, prev_revenue, kept_revenue, nrr}]. 지난달 매출이 있던 고객의 이번 달 매출 ÷ 지난달 매출."""
    need(table, user, time, amount)
    t = table.select(u=table[user], m=bucket_index(table[time], "month"), amt=table[amount].fill_null(0))
    t = t.filter(t.u.notnull(), t.m.notnull())
    per = t.group_by(["u", "m"]).aggregate(r=t.amt.sum())
    paid = per.filter(per.r > 0)
    prev = paid.select("u", m=paid.m + 1, rp=paid.r)
    both = per.join(prev, ["u", "m"])
    kept = {int(r["m"]): r["k"] for r in rows(both.group_by("m").aggregate(k=both.r.sum()))}
    last = int(rows(per.aggregate(m=per.m.max()))[0]["m"])
    totals = {int(r["m"]): r["s"] for r in rows(prev.group_by("m").aggregate(s=prev.rp.sum())) if int(r["m"]) <= last}
    return [{"month": bucket_start(m, "month").isoformat(), "prev_revenue": float(s), "kept_revenue": float(kept.get(m, 0)), "nrr": ratio(float(kept.get(m, 0)), float(s))}
            for m, s in sorted(totals.items())]


# ---------- 분포 ----------

def fetch_column(table, column, limit=MAX_FETCH_ROWS):
    """표의 숫자 컬럼을 빈 값을 빼고 numpy로 가져온다. 행이 limit보다 많으면 표본을 가져온다. (배열, 표본 여부, 전체 행 수)."""
    need(table, column)
    col = table[column]
    if not (col.type().is_numeric() and not col.type().is_boolean()):
        raise ValueError(f"'{column}' 컬럼은 숫자여야 합니다.")
    total = int(rows(table.aggregate(n=table.count()))[0]["n"])
    t = table.filter(col.notnull())
    sampled = total > limit
    if sampled:
        t = t.sample(min(1.0, limit * 1.05 / total)).limit(limit)
    values = np.array([r["v"] for r in rows(t.select(v=t[column].cast("float64")))], dtype=float)
    return values, sampled, total


def quantiles(values, qs=Q):
    """분위수 {q: 값}(선형 보간). 값이 없으면 빈 dict."""
    v = np.asarray(values, dtype=float)
    return {q: float(np.quantile(v, q)) for q in qs} if len(v) else {}


def gini(values):
    """지니 계수 = (2Σ i·x_(i)) / (nΣx) − (n+1)/n (x 오름차순, i=1..n). 모두 같으면 0. 음수가 있거나 합이 0이면 None."""
    x = np.sort(np.asarray(values, dtype=float))
    n = len(x)
    if n == 0 or (x < 0).any() or x.sum() == 0:
        return None
    i = np.arange(1, n + 1)
    return float(2 * (i * x).sum() / (n * x.sum()) - (n + 1) / n)


def pareto_share(values, top=0.2):
    """값이 큰 상위 top 비율 항목(올림)이 전체 합에서 차지하는 몫. 합이 0이면 None."""
    x = np.sort(np.asarray(values, dtype=float))[::-1]
    if len(x) == 0 or not 0 < top <= 1 or x.sum() == 0:
        return None
    return float(x[:math.ceil(len(x) * top - 1e-9)].sum() / x.sum())


def tier_shares(values, whale_min, dolphin_min):
    """Whale(≥ whale_min)·Dolphin(≥ dolphin_min)·Minnow(그 아래)별 사람 수·합계와 몫 {이름: {users, user_share, total, total_share}}. 기준 금액은 분위수나 직접 적은 값이다."""
    if whale_min < dolphin_min:
        raise ValueError("Whale 기준은 Dolphin 기준 이상이어야 합니다.")
    x = np.asarray(values, dtype=float)
    masks = {"whale": x >= whale_min, "dolphin": (x >= dolphin_min) & (x < whale_min), "minnow": x < dolphin_min}
    total = float(x.sum())
    return {name: {"users": int(m.sum()), "user_share": ratio(int(m.sum()), len(x)), "total": float(x[m].sum()), "total_share": ratio(float(x[m].sum()), total)}
            for name, m in masks.items()}


# ---------- 생존 ----------

def kaplan_meier(durations, observed):
    """Kaplan-Meier 생존 곡선. 사건 시각마다 S ×= (1 − d/n)(n = 그 시각 이상 남은 수, 중도절단은 사건이 아니라 n에서만 빠진다).
    결과 {times, survival, at_risk, events, median}. median = S ≤ 0.5가 되는 첫 시각(없으면 None)."""
    d = np.asarray(durations, dtype=float)
    e = np.asarray(observed, dtype=bool)
    if len(d) == 0 or len(d) != len(e):
        raise ValueError("기간과 사건 여부는 같은 길이의 비어 있지 않은 목록이어야 합니다.")
    if (d < 0).any():
        raise ValueError("기간은 0 이상이어야 합니다.")
    times, events = np.unique(d[e], return_counts=True)  # 정렬 한 번 + 이진 탐색으로 O(N log N)
    at_risk = len(d) - np.searchsorted(np.sort(d), times, side="left")  # 그 시각 이상 남은 수
    survival = np.cumprod(1 - events / at_risk) if len(times) else np.array([])
    below = np.nonzero(survival <= 0.5)[0]
    return {"times": times.tolist(), "survival": survival.tolist(), "at_risk": at_risk.tolist(), "events": events.tolist(),
            "median": float(times[below[0]]) if len(below) else None}


# ---------- 예측 평가 ----------

def binary(y, name):
    """0/1(참거짓) 배열로."""
    a = np.asarray(y)
    if a.ndim != 1 or len(a) == 0 or not np.isin(a, (0, 1)).all():
        raise ValueError(f"{name}은 0과 1(또는 참거짓)만 든 비어 있지 않은 목록이어야 합니다.")
    return a.astype(bool)


def confusion(y_true, y_pred):
    """혼동 행렬 {tp, fp, fn, tn}."""
    t, p = binary(y_true, "정답"), binary(y_pred, "예측")
    if len(t) != len(p):
        raise ValueError("정답과 예측의 길이가 다릅니다.")
    return {"tp": int((t & p).sum()), "fp": int((~t & p).sum()), "fn": int((t & ~p).sum()), "tn": int((~t & ~p).sum())}


def average_precision(y_true, score):
    """PR-AUC를 평균 정밀도로: 점수가 큰 순으로 임계값을 내리며 Σ (R_n − R_(n−1))·P_n. 점수가 같은 것은 한꺼번에 넘는다. 정답에 1이 없으면 None."""
    t = binary(y_true, "정답")
    s = np.asarray(score, dtype=float)
    if len(t) != len(s):
        raise ValueError("정답과 점수의 길이가 다릅니다.")
    if not t.any():
        return None
    order = np.argsort(-s, kind="stable")
    t, s = t[order], s[order]
    ends = np.nonzero(np.append(s[1:] != s[:-1], True))[0]  # 같은 점수 묶음의 마지막 자리
    tp = np.cumsum(t)[ends]
    precision = tp / (ends + 1)
    recall = tp / t.sum()
    return float((np.diff(np.concatenate(([0.0], recall))) * precision).sum())


def classification_metrics(y_true, y_pred=None, score=None, threshold=0.5):
    """분류 지표 {tp, fp, fn, tn, precision, recall, f1, accuracy, pr_auc}. y_pred가 없으면 score ≥ threshold로 예측한다. score가 없으면 pr_auc는 None."""
    if y_pred is None:
        if score is None:
            raise ValueError("예측(y_pred)이나 점수(score)가 있어야 합니다.")
        y_pred = np.asarray(score, dtype=float) >= threshold
    c = confusion(y_true, y_pred)
    precision, recall = ratio(c["tp"], c["tp"] + c["fp"]), ratio(c["tp"], c["tp"] + c["fn"])
    f1 = None if precision is None or recall is None else (0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall))  # 둘 다 0이면 0
    return {**c, "precision": precision, "recall": recall, "f1": f1, "accuracy": ratio(c["tp"] + c["tn"], sum(c.values())),
            "pr_auc": average_precision(y_true, score) if score is not None else None}


def check_k(k):
    """K가 1 이상의 정수인지 본다."""
    if isinstance(k, bool) or not isinstance(k, int) or k < 1:
        raise ValueError("K는 1 이상의 정수여야 합니다.")


def dcg(relevances, k):
    """DCG@K = Σ rel_i / log2(i+1) (i=1..K)."""
    r = np.asarray(list(relevances)[:k], dtype=float)
    return float((r / np.log2(np.arange(2, len(r) + 2))).sum())


def ndcg_at_k(ranked, k, all_relevances=None):
    """NDCG@K = DCG / IDCG. ranked는 추천 순서대로의 관련도, 이상적인 순서는 all_relevances(없으면 ranked)를 큰 순으로 놓은 것. IDCG가 0이면 None."""
    check_k(k)
    ideal = dcg(sorted(all_relevances if all_relevances is not None else ranked, reverse=True), k)
    return ratio(dcg(ranked, k), ideal)


def ap_at_k(hits, k, total_relevant=None):
    """AP@K = Σ (i번째가 맞을 때 P@i) / min(K, 맞는 것 전체 수). hits는 순서대로의 맞음(0/1), 전체 수를 안 주면 hits의 합. 맞는 것이 없으면 None."""
    check_k(k)
    h = binary(hits, "맞음 목록")
    top = h[:k]
    precision_at = np.cumsum(top) / np.arange(1, len(top) + 1)
    return ratio(float(precision_at[top].sum()), min(k, int(h.sum()) if total_relevant is None else total_relevant))


def mean_defined(values):
    """None을 뺀 평균(MAP·평균 NDCG용). 하나도 없으면 None."""
    kept = [v for v in values if v is not None]
    return sum(kept) / len(kept) if kept else None


def regression_metrics(y_true, y_pred):
    """RMSE·MAE·MAPE {n, rmse, mae, mape, excluded}. MAPE는 정답 0인 행을 빼고(excluded = 뺀 수) 비율(0.1 = 10%)로 낸다."""
    t, p = np.asarray(y_true, dtype=float), np.asarray(y_pred, dtype=float)
    if len(t) == 0 or len(t) != len(p):
        raise ValueError("정답과 예측은 같은 길이의 비어 있지 않은 목록이어야 합니다.")
    err = p - t
    keep = t != 0
    return {"n": int(len(t)), "rmse": float(np.sqrt((err ** 2).mean())), "mae": float(np.abs(err).mean()),
            "mape": float(np.abs(err[keep] / t[keep]).mean()) if keep.any() else None, "excluded": int((~keep).sum())}


# ---------- 실험 통계 ----------

def norm_cdf(x):
    """표준정규분포의 누적확률."""
    return 0.5 * math.erfc(-x / math.sqrt(2))


# Acklam의 역정규 근사(상대 오차 약 1e-9)
ACK_A = (-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02, 1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00)
ACK_B = (-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02, 6.680131188771972e+01, -1.328068155288572e+01)
ACK_C = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00, -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00)
ACK_D = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00, 3.754408661907416e+00)
ACK_LOW = 0.02425  # 가운데와 꼬리를 가르는 확률


def poly(coefs, x):
    """계수(높은 차수부터)로 다항식 값."""
    out = 0.0
    for c in coefs:
        out = out * x + c
    return out


def norm_ppf(p):
    """표준정규분포의 역누적(Acklam 근사). 0 < p < 1."""
    if not 0 < p < 1:
        raise ValueError("확률은 0과 1 사이여야 합니다.")
    if p < ACK_LOW or p > 1 - ACK_LOW:
        q = math.sqrt(-2 * math.log(min(p, 1 - p)))
        x = poly(ACK_C, q) / (poly(ACK_D, q) * q + 1)
        return x if p < 0.5 else -x
    q = p - 0.5
    r = q * q
    return poly(ACK_A, r) * q / (poly(ACK_B, r) * r + 1)


def lift(treatment, control):
    """Lift = (T − C) / C. C가 0이면 None."""
    return ratio(treatment - control, control)


def two_proportion_z(x1, n1, x2, n2):
    """두 비율 z 검정(합친 비율, 양측) {p1, p2, diff, lift, z, p}. p = erfc(|z| / √2). 1이 대조군, 2가 실험군."""
    if min(n1, n2) <= 0 or not (0 <= x1 <= n1 and 0 <= x2 <= n2):
        raise ValueError("표본 수는 0보다 크고 성공 수는 0 이상 표본 수 이하여야 합니다.")
    p1, p2, pool = x1 / n1, x2 / n2, (x1 + x2) / (n1 + n2)
    se = math.sqrt(pool * (1 - pool) * (1 / n1 + 1 / n2))
    z = (p2 - p1) / se if se else None
    return {"p1": p1, "p2": p2, "diff": p2 - p1, "lift": lift(p2, p1), "z": z, "p": math.erfc(abs(z) / math.sqrt(2)) if z is not None else None}


def lentz(d):
    """연분수 한 항의 되돌림 값(0 근처는 아주 작은 수로)."""
    return d if abs(d) > 1e-300 else 1e-300


def betacf(a, b, x):
    """정규화 불완전 베타의 연분수(Lentz 방법)."""
    qab, qap, qam = a + b, a + 1, a - 1
    c, d = 1.0, 1 / lentz(1 - qab * x / qap)
    h = d
    for m in range(1, 500):
        m2 = 2 * m
        step = 1.0
        for term in (m * (b - m) * x / ((qam + m2) * (a + m2)), -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))):
            d = 1 / lentz(1 + term * d)
            c = lentz(1 + term / c)
            step = d * c
            h *= step
        if abs(step - 1) < 1e-15:
            break
    return h


def betai(a, b, x):
    """정규화 불완전 베타 I_x(a, b)."""
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    front = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1) / (a + b + 2):
        return front * betacf(a, b, x) / a
    return 1 - front * betacf(b, a, 1 - x) / b


def t_two_sided_p(t, df):
    """t분포의 양측 p값 = I_{df/(df+t²)}(df/2, 1/2)."""
    return betai(df / 2, 0.5, df / (df + t * t))


def welch_from_stats(mean1, sd1, n1, mean2, sd2, n2):
    """Welch t 검정(요약값에서, 양측) {diff, t, df, p}. 1이 대조군, 2가 실험군, sd는 표본 표준편차(n−1)."""
    if min(n1, n2) < 2:
        raise ValueError("각 집단의 표본은 2개 이상이어야 합니다.")
    v1, v2 = sd1 ** 2 / n1, sd2 ** 2 / n2
    if v1 + v2 == 0:
        raise ValueError("두 집단의 값이 모두 같아 검정할 수 없습니다.")
    t = (mean2 - mean1) / math.sqrt(v1 + v2)
    df = (v1 + v2) ** 2 / (v1 ** 2 / (n1 - 1) + v2 ** 2 / (n2 - 1))
    return {"diff": mean2 - mean1, "t": t, "df": df, "p": t_two_sided_p(t, df)}


def welch_t_test(control, treatment):
    """Welch t 검정(값 배열에서)."""
    a, b = np.asarray(control, dtype=float), np.asarray(treatment, dtype=float)
    if min(len(a), len(b)) < 2:
        raise ValueError("각 집단의 표본은 2개 이상이어야 합니다.")
    return welch_from_stats(float(a.mean()), float(a.std(ddof=1)), len(a), float(b.mean()), float(b.std(ddof=1)), len(b))


def check_alpha(alpha, power=0.8):
    """유의수준·검정력이 0과 1 사이인지 본다."""
    if not (0 < alpha < 1 and 0 < power < 1):
        raise ValueError("유의수준과 검정력은 0과 1 사이여야 합니다.")


def power_normal(effect, se, alpha=0.05):
    """정규 근사 검정력(양측): Φ(|효과|/se − z) + Φ(−|효과|/se − z), z = Φ⁻¹(1 − α/2)."""
    check_alpha(alpha)
    z, shift = norm_ppf(1 - alpha / 2), abs(effect) / se
    return norm_cdf(shift - z) + norm_cdf(-shift - z)


def mde_normal(se, alpha=0.05, power=0.8):
    """정규 근사 MDE = (z_(1−α/2) + z_power) × se."""
    check_alpha(alpha, power)
    return (norm_ppf(1 - alpha / 2) + norm_ppf(power)) * se


def prop_se(p1, p2, n1, n2):
    """두 비율 차이의 표준오차(합치지 않은 분산)."""
    if min(n1, n2) <= 0:
        raise ValueError("표본 수는 0보다 커야 합니다.")
    return math.sqrt(p1 * (1 - p1) / n1 + p2 * (1 - p2) / n2)


def power_two_proportions(p1, p2, n1, n2=None, alpha=0.05):
    """두 비율 검정의 검정력(정규 근사)."""
    return power_normal(p2 - p1, prop_se(p1, p2, n1, n2 or n1), alpha)


def mde_two_proportions(p1, n1, n2=None, alpha=0.05, power=0.8):
    """대조군 비율 p1에서 검출할 수 있는 최소 차이(MDE, 정규 근사). 두 집단의 분산을 p1으로 본다."""
    return mde_normal(prop_se(p1, p1, n1, n2 or n1), alpha, power)


def power_two_means(delta, sd, n1, n2=None, alpha=0.05):
    """두 평균 검정의 검정력(정규 근사, 표준편차 sd 공통)."""
    return power_normal(delta, sd * math.sqrt(1 / n1 + 1 / (n2 or n1)), alpha)


def mde_two_means(sd, n1, n2=None, alpha=0.05, power=0.8):
    """두 평균 검정의 MDE(정규 근사, 표준편차 sd 공통)."""
    return mde_normal(sd * math.sqrt(1 / n1 + 1 / (n2 or n1)), alpha, power)
