"""생키(흐름) 카드의 단계 쌍별 인원: core/funnel.py의 reached(단계마다 도달한 사람)를 그대로 쓰고, 이웃한 두 단계 사이를 지난 사람과 빠진 사람을 센다.
단계 i에서 i+1로 간 사람 = 단계 i+1에 도달한 사람(reached는 앞 단계를 거친 사람만 남긴다)이고, 단계 i에서 빠진 사람 = (단계 i 사람 - 단계 i+1 사람)이다.
자동 흐름(Phase 3, analysis.mode = auto)은 사용자별 시각 순 이벤트의 이어진 이동을 창 함수 없이(reached처럼 작은 집계·조인을 단계 수만큼) 센다(plans/phase-3.md#auto_walk)."""
import datetime as dt

from lazy import ibis

from core import funnel
from core import result

OUT = "이탈"  # 빠진 사람을 모으는 마지막 칸의 이름
AUTO = "auto"  # source.analysis.mode: 자동 흐름(Phase 3). 없으면 지금처럼 사용자가 적은 단계(steps)
AUTO_DEPTH = 6  # 자동 흐름 단계 깊이의 기본값(analysis.depth가 없을 때)
MIN_DEPTH, MAX_DEPTH = 2, 50  # 자동 흐름 단계 깊이 범위
AUTO_ROLES = (("starts", "시작"), ("middles", "중간"), ("ends", "끝"))  # 자동 흐름의 값 목록 역할(키, 오류 문구에 쓰는 말)
MAX_PICKS = result.MAX_VALUES  # 시작·중간·끝 값 목록 하나에 고를 수 있는 값 수(값 목록 기능의 상한과 같다)
MAX_FLOW_NODES = 200  # 자동 흐름 칸((단계, 값)) 수 상한. 넘으면 ValueError(카드 view.flow_unlimited면 상한 없음)
MAX_FLOW_LINKS = 400  # 자동 흐름 띠((단계, 값) → (다음 단계, 값), 이탈 띠 포함) 수 상한. 넘으면 ValueError(view.flow_unlimited면 상한 없음)
TOO_MANY = "흐름 칸이나 띠가 너무 많아 그리지 않았어요({what} {n:,}개 넘음). 시작·중간·끝 값을 골라 줄이거나 단계 깊이를 줄이세요. 카드 설정에서 '상한 없음'을 고를 수도 있어요."


def pairs(base, steps, window_seconds=None, entry=None):
    """사용자 u·단계 s·시각 t 열이 있는 표 base와 단계 값(steps, 컬럼 종류에 맞춘 값)으로 {nodes: [{name, users}], links: [{s, t, v}]}.
    nodes는 단계 차례 + (빠진 사람이 있을 때만) 마지막 '이탈' 칸이고, links의 s·t는 nodes의 번호다: 이웃한 단계 쌍마다 (i → i+1, 지난 사람)과 빠진 사람이 있는 단계의 (i → 이탈, 빠진 사람).
    window_seconds·entry는 funnel.reached와 같다."""
    users = funnel.counts(funnel.reached(base, steps, window_seconds, entry))
    nodes = [{"name": str(s), "users": n} for s, n in zip(steps, users)]
    links = [{"s": i, "t": i + 1, "v": users[i + 1]} for i in range(len(steps) - 1)]
    lost = [(i, users[i] - users[i + 1]) for i in range(len(steps) - 1) if users[i] > users[i + 1]]
    if lost:
        nodes.append({"name": OUT, "users": sum(n for _, n in lost)})
        links += [{"s": i, "t": len(nodes) - 1, "v": n} for i, n in lost]
    return {"nodes": nodes, "links": links}


def build(table, user, step, time, steps, window_seconds=None, entry=None):
    """카드의 조회 결과 table에서 사용자·단계·시각 컬럼을 funnel.compute와 같이 검사해 읽고(단계는 2개 이상) values 모양의 pairs를 돌려준다."""
    steps, window_seconds = funnel.check(table, user, step, time, steps, window_seconds)
    if len(steps) < 2:
        raise ValueError("생키 차트에는 단계를 2개 이상 골라 주세요.")
    values = [funnel.step_value(table[step].type(), s) for s in steps]
    base = table.select(u=table[user], s=table[step], t=table[time].cast("timestamp"))
    return {"values": pairs(base.filter(base.u.notnull(), base.t.notnull()), values, window_seconds, entry)}


def check_auto(table: "ibis.expr.types.Table", how: dict) -> dict:
    """[SPECIFICATION] plans/phase-3.md#check_auto
    자동 흐름의 source.analysis(user·step·time·starts·middles·ends·depth)를 검사해 {user, step, time, starts, middles, ends, depth}로 정리한다. 틀리면 이유를 ValueError로."""
    funnel.check(table, how.get("user"), how.get("step"), how.get("time"), [AUTO], None)  # 단계 목록이 없어 자리 값 하나로 컬럼·시각 타입 검사만 쓴다
    picks = {}
    for key, word in AUTO_ROLES:
        v = how.get(key) or []
        if not (isinstance(v, list) and all(isinstance(x, str) and x for x in v) and len(v) <= MAX_PICKS and len(set(v)) == len(v)):
            raise ValueError(f"{word} 값은 겹치지 않는 글자 목록이며 {MAX_PICKS}개까지예요.")
        picks[key] = v
    seen = set()
    for value in (x for v in picks.values() for x in v):
        if value in seen:
            raise ValueError(f"'{value}'을 두 역할(시작·중간·끝)에 고를 수 없어요.")
        seen.add(value)
    depth = how.get("depth", AUTO_DEPTH)
    if not (type(depth) is int and MIN_DEPTH <= depth <= MAX_DEPTH):
        raise ValueError(f"단계 깊이는 {MIN_DEPTH}~{MAX_DEPTH} 사이의 정수예요.")
    return {"user": how.get("user"), "step": how.get("step"), "time": how.get("time"), **picks, "depth": depth}


def auto_walk(base: "ibis.expr.types.Table", starts: list[str], middles: list[str], ends: list[str], depth: int,
              entry: tuple[dt.date | None, dt.date | None] | None = None) -> "ibis.expr.types.Table":
    """[SPECIFICATION] plans/phase-3.md#auto_walk
    (u, e, t) 표에서 고른 값만 남기고 시작~끝으로 자른 뒤, 사용자별로 (시각, 값) 차례의 다음 '다른 값'을 단계마다 집계·조인으로 찾아(창 함수 없음) (u, rn, e, nxt) 식(rn 0부터, 깊이 끝 칸의 nxt도 한 단계 더 보고 채움, 실행하지 않음)."""
    b = base
    kept = [*starts, *middles, *ends]
    preds = [b.e.isin(kept)] if kept else []  # 고르지 않은 값은 빼고 앞뒤를 잇는다(건너뛰기)
    if entry:  # 대시보드 기간: 모든 행에
        day = b.t.cast("date")
        preds += [*([day >= entry[0]] if entry[0] else []), *([day <= entry[1]] if entry[1] else [])]
    if preds:  # filter([])는 오류
        b = b.filter(preds)
    if starts:  # 처음 시작 값을 한 때부터(그 앞은 자름). 시작 값이 없는 사용자는 빠진다
        first = b.filter(b.e.isin(starts)).group_by("u").aggregate(t0=ibis._.t.min())
        b = b.join(first, "u").filter(ibis._.t >= ibis._.t0).select("u", "e", "t")
    if ends:  # 처음 끝 값에서 끝냄(뒤는 자름). 끝 값이 없는 사용자는 끝까지
        stop = b.filter(b.e.isin(ends)).group_by("u").aggregate(t1=ibis._.t.min())
        b = b.left_join(stop, "u")
        b = b.filter(b.t1.isnull() | (b.t <= b.t1)).select("u", "e", "t")

    # 창 함수 없이(2차 결정 6): 단계마다 "사용자별 (t, e) 차례로 지금 칸 뒤의, 지금 값과 다른 첫 행"을 집계·조인으로 찾는다(funnel.reached와 같은 방식).
    def first_row(rows):  # 사용자마다 (t, e) 차례로 가장 앞 행 하나: 가장 이른 시각, 같은 시각이면 값 글자가 작은 것
        m = rows.group_by("u").aggregate(tk=rows.t.min())
        j = rows.join(m, "u").filter(ibis._.t == ibis._.tk)
        return j.group_by("u").aggregate(tk=ibis._.tk.min(), ek=ibis._.e.min())

    levels = [first_row(b)]
    for _ in range(depth):  # 단계 수만큼의 반복(데이터 행 반복 아님). 깊이 + 1번째 칸은 미리 보기 전용
        prev = levels[-1]  # 사용자마다 한 줄이라 사용자 키 조인으로 행이 불지 않는다
        after = b.join(prev, "u").filter(((ibis._.t > ibis._.tk) | ((ibis._.t == ibis._.tk) & (ibis._.e > ibis._.ek))) & (ibis._.e != ibis._.ek))
        levels.append(first_row(after.select("u", "e", "t")))  # 같은 값이 이어진 행은 건너뛰어 하나로 합쳐진다
    parts = []
    for k in range(depth):  # 보이는 칸은 rn 0..depth-1. 마지막 칸의 nxt도 levels[depth]로 채운다(깊이로 잘린 것과 경로가 끝난 것을 가른다)
        nxt = levels[k + 1].select("u", nxt="ek")
        parts.append(levels[k].left_join(nxt, "u").select("u", rn=ibis.literal(k, "int64"), e="ek", nxt="nxt"))
    walk = parts[0]
    for p in parts[1:]:
        walk = walk.union(p)  # UNION ALL
    return walk  # 열 u, rn(0부터), e, nxt


def auto_build(table: "ibis.expr.types.Table", how: dict, entry: tuple[dt.date | None, dt.date | None] | None = None, unlimited: bool = False,
               dropout: bool | None = None) -> dict:
    """[SPECIFICATION] plans/phase-3.md#auto_build
    카드의 조회 결과 table에서 자동 흐름을 DB에서 세어(칸·띠마다 고유 사용자 수, dropout이 켜졌으면(None이면 끝 값을 골랐을 때만) 뒤에 고른 값이 더 없고 끝 값도 아닌 칸에서 멈춘 사람의 마지막 '이탈' 칸 하나) {values: {nodes: [{name, step, users}], links: [{s, t, v}]}}를 돌려준다."""
    c = check_auto(table, how)
    depth = c["depth"]
    base = table.select(u=table[c["user"]], e=table[c["step"]].cast("string"), t=table[c["time"]].cast("timestamp"))
    base = base.filter(base.u.notnull(), base.e.notnull(), base.t.notnull())
    walk = auto_walk(base, c["starts"], c["middles"], c["ends"], depth, entry)
    lk = walk.filter(walk.nxt.notnull(), walk.rn < depth - 1).group_by(["rn", "e", "nxt"]).aggregate(v=ibis._.u.nunique()).order_by([ibis.desc("v"), "rn", "e", "nxt"])
    nd = walk.group_by(["rn", "e"]).aggregate(users=ibis._.u.nunique()).order_by([ibis.desc("users"), "rn", "e"])
    gone = [walk.nxt.isnull(), *([~walk.e.isin(c["ends"])] if c["ends"] else [])]  # 뒤에 보이는 값이 없고, 끝 값 칸에서 멈춘 것이 아님
    out = walk.filter(gone).group_by(["rn", "e"]).aggregate(v=ibis._.u.nunique()).order_by([ibis.desc("v"), "rn", "e"])

    def cut(q, n):  # 상한이 있을 때만 하나 더 받아 넘쳤는지 안다
        return q if unlimited else q.limit(n + 1)

    links, nodes = cut(lk, MAX_FLOW_LINKS).to_pyarrow().to_pylist(), cut(nd, MAX_FLOW_NODES).to_pyarrow().to_pylist()
    dropout = bool(c["ends"]) if dropout is None else dropout
    outs = cut(out, MAX_FLOW_LINKS).to_pyarrow().to_pylist() if dropout else []
    if not unlimited:
        if len(nodes) + (1 if outs else 0) > MAX_FLOW_NODES:
            raise ValueError(TOO_MANY.format(what="칸", n=MAX_FLOW_NODES))
        if len(links) + len(outs) > MAX_FLOW_LINKS:
            raise ValueError(TOO_MANY.format(what="띠", n=MAX_FLOW_LINKS))
    nodes.sort(key=lambda r: (r["rn"], -r["users"], r["e"]))
    links.sort(key=lambda r: (r["rn"], -r["v"], r["e"], r["nxt"]))
    outs.sort(key=lambda r: (r["rn"], -r["v"], r["e"]))
    at = {(r["rn"], r["e"]): i for i, r in enumerate(nodes)}
    shown = [{"name": r["e"], "step": r["rn"] + 1, "users": int(r["users"])} for r in nodes]
    paths = [{"s": at[(r["rn"], r["e"])], "t": at[(r["rn"] + 1, r["nxt"])], "v": int(r["v"])} for r in links]
    if outs:
        shown.append({"name": OUT, "step": None, "users": sum(int(r["v"]) for r in outs)})
        paths += [{"s": at[(r["rn"], r["e"])], "t": len(shown) - 1, "v": int(r["v"])} for r in outs]
    return {"values": {"nodes": shown, "links": paths}}
