"""생키(흐름) 카드의 단계 쌍별 인원: core/funnel.py의 reached(단계마다 도달한 사람)를 그대로 쓰고, 이웃한 두 단계 사이를 지난 사람과 빠진 사람을 센다.
단계 i에서 i+1로 간 사람 = 단계 i+1에 도달한 사람(reached는 앞 단계를 거친 사람만 남긴다)이고, 단계 i에서 빠진 사람 = (단계 i 사람 - 단계 i+1 사람)이다."""
from core import funnel

OUT = "이탈"  # 빠진 사람을 모으는 마지막 칸의 이름


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
