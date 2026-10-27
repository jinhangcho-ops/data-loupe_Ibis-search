// 흐름 보기(6단계): 검색 body를 단계(node)와 이음(edge)으로 그린 그림. 단계 카드·흐름·문장 보기는 같은 검색 body를 본다(화면 쪽 변환은 index.html의 setView).
// 위쪽은 화면 없이 시험하는 계산(배치·이음 규칙·글·그래프 바꾸기)이고, 아래쪽 FlowView가 SVG 그림·끌기·팔레트·최적화 비교를 맡는다. 이음을 해도 되는지의 최종 판단은 서버(/api/flow/check_edge)가 하고
// 여기 flowEdgeLocal은 모양·순서·순환만 미리 본다(끌 때 바로 막으려고). common.js의 el·api·confirmScan과 SERVER.flow(서버 flow.py의 순위·이름)가 먼저 있어야 한다.
const FLOW = { W: 176, H: 56, GX: 84, GY: 30, PAD: 28, TAIL: ["aggregate", "metric", "chart", "card"], ENDS: ["chart", "card"] };
const FLOW_ADD = ["table", "union", "join", "period", "condition", "calc", "dedupe", "aggregate", "metric", "chart", "card"];  // 팔레트 차례
const flowRank = kind => SERVER.flow.ranks[kind];
const flowName = kind => SERVER.flow.names[kind] ?? kind;
const flowNode = (g, id) => g.nodes.find(n => n.id === id);
const flowIns = (g, id) => g.edges.filter(e => e[1] === id).map(e => e[0]);
const flowOuts = (g, id) => g.edges.filter(e => e[0] === id).map(e => e[1]);
const flowSame = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const flowCopy = g => JSON.parse(JSON.stringify(g));

// start에서 이음을 따라 goal까지 가는 가장 짧은 단계 id 목록(없으면 null). 순환 검사에 쓴다(서버 flow.route와 같다).
function flowRoute(g, start, goal) {
  const from = { [start]: null }, queue = [start];
  while (queue.length) {
    let now = queue.shift();
    if (now === goal) { const path = []; for (; now != null; now = from[now]) path.unshift(now); return path; }
    for (const next of flowOuts(g, now)) if (!(next in from)) { from[next] = now; queue.push(next); }
  }
  return null;
}
// 단계 앞쪽(자기 포함)의 모든 단계 id: 집계·카드를 누르면 이 경로를 밝힌다. 순환이 있어도 끝난다.
function flowUpstream(g, id) {
  const seen = new Set(), visit = i => { if (!seen.has(i)) { seen.add(i); flowIns(g, i).forEach(visit); } };
  visit(id);
  return [...seen];
}
// 단계 이름 글(서버 flow.label과 같다): 테이블은 ‘이름’, 나머지는 종류(id)
const flowLabel = n => (n.kind === "table" && n.params?.table ? `${flowName(n.kind)} ‘${n.params.table}’` : `${flowName(n.kind)}(${n.id})`);
// 이음 a→b를 해도 되는지 모양·순서·순환만 본 이유(서버 flow.edge_problem의 앞부분과 같다). 되면 "".
function flowEdgeLocal(g, a, b) {
  const A = flowNode(g, a), B = flowNode(g, b);
  if (!A || !B) return "이을 단계가 흐름에 없어요.";
  if (a === b) return "단계를 자기 자신에게 이을 수 없어요.";
  if (g.edges.some(e => e[0] === a && e[1] === b)) return "이미 이어져 있어요.";
  const na = flowName(A.kind), nb = flowName(B.kind);
  if (B.kind === "table") return `${na} 뒤에 테이블을 이을 수 없어요. 테이블은 흐름의 시작이에요.`;
  if (FLOW.ENDS.includes(A.kind)) return `${na} 뒤에는 다른 단계를 이을 수 없어요. 차트·카드는 흐름의 끝이에요.`;
  const open = ["join", "union"].includes(B.kind);  // 조인·이어붙이기는 조건을 건 테이블 같은 가지도 받는다
  if (!open && (flowRank(B.kind) < flowRank(A.kind) || (flowRank(B.kind) === flowRank(A.kind) && !SERVER.flow.same_rank.includes(A.kind)))) return `${na} 뒤에 ${nb} 단계를 놓을 수 없어요. 순서는 ${SERVER.flow.order}예요.`;
  const path = flowRoute(g, b, a);
  if (path) return "순환이 생겨요: " + [...path, b].map(i => flowLabel(flowNode(g, i))).join(" → ") + ".";
  const have = flowIns(g, b);
  if (B.kind === "union" && A.kind !== "table") return "이어붙이기에는 테이블끼리만 이을 수 있어요.";
  if (B.kind === "join" && have.length >= 2) return "조인에는 왼쪽·오른쪽 두 곳만 이을 수 있어요.";
  if (!open && have.length) return `${nb}에는 이미 앞 단계(${flowLabel(flowNode(g, have[0]))})가 이어져 있어요. 한 단계에는 하나만 이을 수 있어요.`;
  return "";
}
// 이음 a→b 사이에 끼울 수 있는 단계 종류. 순서가 맞는 것만(조인·테이블·이어붙이기·차트·카드는 끼우지 않는다).
function flowInsertKinds(g, a, b) {
  const ka = flowNode(g, a).kind, kb = flowNode(g, b).kind, open = ["join", "union"].includes(kb), same = SERVER.flow.same_rank;
  return ["period", "condition", "calc", "dedupe", "aggregate", "metric"].filter(k => {
    const after = flowRank(k) > flowRank(ka) || (flowRank(k) === flowRank(ka) && same.includes(ka));
    const before = open || flowRank(k) < flowRank(kb) || (flowRank(k) === flowRank(kb) && same.includes(k));
    return after && before;
  });
}
// 단계마다 칸(깊이)과 줄을 정한 자리: 깊이 = 앞쪽에서 가장 긴 길, 같은 칸 안에서는 앞 단계들의 평균 줄 순서(교차를 줄인다). 순환이 있어도 끝난다.
function flowLayout(g) {
  const depth = {}, visit = (id, stack = []) => {
    if (id in depth) return depth[id];
    if (stack.includes(id)) return 0;
    return (depth[id] = Math.max(-1, ...flowIns(g, id).map(i => visit(i, [...stack, id]))) + 1);
  };
  g.nodes.forEach(n => visit(n.id));
  const cols = {};
  g.nodes.forEach(n => (cols[depth[n.id]] ??= []).push(n.id));
  const row = {}, out = {};
  Object.keys(cols).map(Number).sort((a, b) => a - b).forEach(d => {
    const mean = id => { const ins = flowIns(g, id).filter(i => i in row); return ins.length ? ins.reduce((s, i) => s + row[i], 0) / ins.length : Infinity; };
    cols[d].map((id, k) => [id, mean(id), k]).sort((a, b) => a[1] - b[1] || a[2] - b[2]).forEach(([id], k) => {
      row[id] = k;
      out[id] = { x: FLOW.PAD + d * (FLOW.W + FLOW.GX), y: FLOW.PAD + k * (FLOW.H + FLOW.GY) };
    });
  });
  return out;
}
const flowPlace = g => { const at = flowLayout(g); g.nodes.forEach(n => { n.pos = at[n.id]; }); return g; };
const flowPos = n => ({ x: n.pos?.x ?? FLOW.PAD, y: n.pos?.y ?? FLOW.PAD });
const flowClip = (text, n = 24) => { const s = String(text ?? ""); return s.length > n ? s.slice(0, n - 1) + "…" : s; };
const condText = c => typeof c === "string" ? c : c?.line ?? (c?.any ? c.any.map(condText).join(" or ") : "");
// 단계 안에 보일 짧은 글
function flowNodeText(n) {
  const p = n.params ?? {};
  switch (n.kind) {
    case "table": return p.table ?? "";
    case "union": return p.how ?? "";
    case "join": return `${p.how ?? "inner"} ${(p.keys ?? []).map(k => `${k[0]}=${k[1]}`).join(", ")}`.trim();
    case "period": return p.line ?? `${p.column ?? ""} ${p.start ?? ""} ~ ${p.end ?? ""}`.trim();
    case "condition": return condText(p.cond);
    case "calc": return p.line ?? "";
    case "dedupe": return `${(p.keys ?? []).join(", ")}${p.order ? ` · ${p.order}` : ""}`;
    case "aggregate": return `${(p.by ?? []).join(", ") || "전체"} · ${(p.series ?? []).map(s => s.name ?? s.agg ?? "").join(", ")}`;
    case "metric": return (p.names ?? []).join(", ");
    default: return [p.x, p.split].filter(Boolean).join(" · ");
  }
}
// 새 단계의 기본 params. ctx = { table, columns, dates }(지금 기준 테이블, 앞 단계의 컬럼 이름들, 그 가운데 날짜 컬럼들)
function flowDefault(kind, ctx = {}) {
  const first = (ctx.columns ?? [])[0] ?? "";
  return {
    table: { table: ctx.table ?? "" }, union: { how: SERVER.flow.unions?.[0] ?? "left" }, join: { how: "inner", keys: [] },
    period: { line: (ctx.dates?.[0] ?? first) ? `${ctx.dates?.[0] ?? first} ~ ` : "" }, condition: { cond: first ? `${first} is not null` : "" }, calc: { line: "" }, dedupe: { keys: first ? [first] : [] },
    aggregate: { by: [], series: [{ agg: "count", y: "", name: "건수" }] }, metric: { names: [] }, chart: { x: first }, card: { x: first },
  }[kind];
}
const flowNewId = g => "n" + (1 + Math.max(0, ...g.nodes.map(n => +String(n.id).replace(/\D/g, "") || 0)));
// 단계를 더한 새 흐름과 그 id (이음은 없이)
function flowAdd(g, kind, params, pos) {
  const next = flowCopy(g), id = flowNewId(next);
  next.nodes.push({ id, kind, params: params ?? flowDefault(kind), pos: pos ?? { x: FLOW.PAD, y: FLOW.PAD } });
  return { graph: next, id };
}
// 이음 a→b 사이에 단계를 끼운 새 흐름: 그 이음이 있던 자리(edges 차례, 조인의 왼쪽·오른쪽 순서)에 a→새 단계, 새 단계→b를 놓는다.
function flowInsert(g, a, b, kind, params) {
  const at = g.edges.findIndex(e => e[0] === a && e[1] === b);
  if (at < 0) throw new Error("끼울 이음이 없어요.");
  const A = flowNode(g, a), B = flowNode(g, b), { graph, id } = flowAdd(g, kind, params, { x: (flowPos(A).x + flowPos(B).x) / 2, y: (flowPos(A).y + flowPos(B).y) / 2 });
  graph.edges.splice(at, 1, [a, id], [id, b]);
  return { graph, id };
}
// 단계를 지운 새 흐름. 앞 하나·뒤 하나의 중간 단계면 앞과 뒤를 이어 둔다. 다시 잇는 이음은 뒤 단계로 가던 이음 자리에 놓아 뒤 단계의 입력 차례(조인의 왼쪽·오른쪽)를 지킨다.
function flowRemove(g, id) {
  const next = flowCopy(g), ins = flowIns(g, id), outs = flowOuts(g, id), touch = e => e[0] === id || e[1] === id;
  const k = g.edges.findIndex(e => e[0] === id && e[1] === outs[0]), at = k - g.edges.slice(0, k).filter(touch).length;  // 그 앞에서 빠지는 이음 수만큼 당긴다
  next.nodes = next.nodes.filter(n => n.id !== id);
  next.edges = next.edges.filter(e => !touch(e));
  if (ins.length === 1 && outs.length === 1) next.edges.splice(at, 0, [ins[0], outs[0]]);
  return next;
}
const flowCut = (g, a, b) => ({ ...flowCopy(g), edges: g.edges.filter(e => !(e[0] === a && e[1] === b)).map(e => [...e]) });
// 집계·지표·차트·카드(검색 body에 없는 끝쪽 단계)와 그 사이 이음 / 단계 카드로 바꿀 때의 끝 단계(꼬리 바로 앞)
function flowTail(g) {
  const tail = new Set(g.nodes.filter(n => FLOW.TAIL.includes(n.kind)).map(n => n.id));
  return { nodes: g.nodes.filter(n => tail.has(n.id)), edges: g.edges.filter(e => tail.has(e[0]) && tail.has(e[1])), feed: g.edges.filter(e => !tail.has(e[0]) && tail.has(e[1])).map(e => e[0]) };
}
// 끝 단계에서 첫 입력을 거슬러 처음 만나는 꼬리 아닌 단계(본 흐름 가운데 집계가 있어도 그 뒤 조인까지 넘긴다). 끝이 여럿이면 꼬리로 들어가는 첫 단계.
function flowBodyTarget(g) {
  const ends = g.nodes.filter(n => !flowOuts(g, n.id).length);
  let id = ends.length === 1 ? ends[0].id : undefined;
  for (let i = 0; id && FLOW.TAIL.includes(flowNode(g, id)?.kind) && i <= g.nodes.length; i++) id = flowIns(g, id)[0];
  return id ?? flowTail(g).feed[0];
}
// 새 body로 만든 흐름의 끝에 꼬리를 다시 붙인다(단계 카드를 고치고 돌아왔을 때 집계·카드가 남도록)
function flowAttach(g, tail) {
  if (!tail.nodes.length) return g;
  const next = flowCopy(g), ids = new Set(next.nodes.map(n => n.id)), map = {};
  tail.nodes.forEach(n => { map[n.id] = ids.has(n.id) ? flowNewId({ nodes: [...next.nodes, ...Object.values(map).map(id => ({ id }))] }) : n.id; next.nodes.push({ ...flowCopy(n), id: map[n.id] }); });
  next.edges.push(...tail.edges.map(e => [map[e[0]], map[e[1]]]));
  const roots = tail.nodes.filter(n => !tail.edges.some(e => e[1] === n.id)), sinks = g.nodes.filter(n => !flowOuts(g, n.id).length && !FLOW.TAIL.includes(n.kind));
  if (roots[0] && sinks.length) next.edges.push([sinks[sinks.length - 1].id, map[roots[0].id]]);
  return next;
}
// 조건 목록의 {any: [...]} 묶음을 단계 카드가 쓰는 한 줄씩 항목으로 되돌린다(묶음 안은 or:true). index.html의 grouped()의 반대.
function flowUngroup(conditions) {
  return (conditions ?? []).flatMap(c => c && typeof c === "object" && Array.isArray(c.any)
    ? c.any.map((x, i) => (typeof x === "string" ? (i < c.any.length - 1 ? { line: x, or: true } : x) : { ...x, ...(i < c.any.length - 1 ? { or: true } : {}) })) : [c]);
}
// 최적화 전·후 비교에서 바뀐 단계 id: 새로 생김(add) / 빠짐(del) / params가 바뀜(chg)
function flowDiff(before, after) {
  const marks = {};
  after.nodes.forEach(n => { const old = flowNode(before, n.id); if (!old) marks[n.id] = "add"; else if (!flowSame(old.params, n.params)) marks[n.id] = "chg"; });
  before.nodes.forEach(n => { if (!flowNode(after, n.id)) marks[n.id] = "del"; });
  return marks;
}
// 단계 앞쪽에서 이어 오는 컬럼 {컬럼: 종류}(스키마만 보고 어림한다. 모르면 빈 객체)
function flowColumns(g, id, schemas, depth = 0) {
  const n = flowNode(g, id), p = n?.params ?? {};
  if (!n || depth > 40) return {};
  const ins = flowIns(g, id), from = i => flowColumns(g, i, schemas, depth + 1);
  if (n.kind === "table") return { ...(schemas?.[p.table]?.columns ?? {}) };
  if (n.kind === "join") {
    const left = ins[0] ? from(ins[0]) : {}, all = ins[1] ? from(ins[1]) : {};
    if (["semi", "anti"].includes(p.how)) return left;
    // inner 조인의 같은 이름 키는 하나로 합쳐 빠지고, 겹치는 이름은 _right, 그 이름도 이미 있으면 오른쪽 테이블 이름(서버 flow.join_names·parse.rname_for와 같다)
    const merged = (p.how ?? "inner") === "inner" ? new Set((p.keys ?? []).filter(([l, r]) => l === r).map(([, r]) => r)) : new Set();
    const right = Object.fromEntries(Object.entries(all).filter(([c]) => !merged.has(c)));
    const clash = sfx => Object.keys(right).some(c => Object.hasOwn(left, c) && (Object.hasOwn(left, c + sfx) || Object.hasOwn(right, c + sfx)));
    let sfx = "_right";
    if (clash(sfx)) for (sfx = "_" + (flowRoot(g, ins[1])?.params?.table ?? "").replace(/[^\p{L}\p{N}_]/gu, "_"); clash(sfx); sfx += "_");
    return { ...left, ...Object.fromEntries(Object.entries(right).map(([c, k]) => [Object.hasOwn(left, c) ? c + sfx : c, k])) };
  }
  const base = ins[0] ? from(ins[0]) : {};
  if (n.kind === "union") {  // 서버 flow.out_columns와 같다: left는 첫 입력 컬럼 모두, 나머지는 모든 입력에 있는 컬럼만, left·inner(parse.STACKS)는 '출처'(parse.SOURCE)를 붙인다
    const subs = ins.slice(1).map(from), how = p.how ?? "left";
    const cols = how === "left" ? base : Object.fromEntries(Object.entries(base).filter(([c]) => subs.every(s => Object.hasOwn(s, c))));
    return { ...cols, ...(["left", "inner"].includes(how) ? { 출처: "text" } : {}) };
  }
  if (n.kind === "calc") return { ...base, ...(p.adds ?? {}) };
  // 집계 계열의 종류는 함수 사전의 kind_out(any면 값 컬럼의 종류, 비율이면 num). 사전을 아직 못 받았으면 num으로 어림한다
  const out = s => { const k = s.ratio || typeof Funcs === "undefined" ? "num" : Funcs.get(s.agg ?? "count")?.kind_out ?? "num"; return k === "any" ? base[s.y] ?? "other" : k; };
  if (n.kind === "aggregate") return { ...Object.fromEntries((p.by ?? []).filter(Boolean).map(c => [c, base[c] ?? "other"])), ...Object.fromEntries((p.series ?? []).map((s, i) => [s.name || "v" + i, out(s)])) };  // 이름 없는 계열은 서버와 같이 v0, v1 …
  if (n.kind === "metric") return { ...Object.fromEntries((p.by ? [p.by].flat() : []).map(c => [c, base[c] ?? "other"])), ...Object.fromEntries((p.names ?? []).map(m => [m, "num"])) };
  return base;
}
// 조인 단계의 (왼쪽 뿌리 테이블, 오른쪽 뿌리 테이블) 단계. 앞쪽을 거슬러 처음 만나는 테이블(첫 이음을 따른다)
function flowRoot(g, id, depth = 0) {
  const n = flowNode(g, id);
  if (!n || depth > 40) return null;
  return n.kind === "table" ? n : flowRoot(g, flowIns(g, id)[0], depth + 1);
}
// 이음 곡선의 SVG path 글
function flowCurve(x1, y1, x2, y2) {
  const d = Math.max(30, Math.abs(x2 - x1) / 2);
  return `M${x1},${y1} C${x1 + d},${y1} ${x2 - d},${y2} ${x2},${y2}`;
}
const flowCurveMid = (x1, y1, x2, y2) => ({ x: (x1 + x2) / 2, y: (y1 + y2) / 2 });
const flowBox = g => g.nodes.reduce((m, n) => { const p = flowPos(n); return { w: Math.max(m.w, p.x + FLOW.W + FLOW.PAD), h: Math.max(m.h, p.y + FLOW.H + FLOW.PAD + 14) }; }, { w: 320, h: 160 });

// ---------- SVG 그리기 ----------
// 흐름 그림 하나. o = { sel, hi(밝힐 id 목록), rows({id: 행 수}), marks({id: add·chg·del}), scale, on: { node(id, ev), port(id, ev), plus(a, b, ev), cut(a, b), over(id) } }
function flowSvg(g, o = {}) {
  const { W, H } = FLOW, box = flowBox(g), scale = o.scale ?? 1, on = o.on ?? {}, hi = o.hi ? new Set(o.hi) : null;
  const root = svg("svg", { class: "fsvg", width: box.w * scale, height: box.h * scale, viewBox: `0 0 ${box.w} ${box.h}` });
  const edges = svg("g"), nodes = svg("g");
  g.edges.forEach(([a, b], k) => {
    const A = flowNode(g, a), B = flowNode(g, b);
    if (!A || !B) return;
    const pa = flowPos(A), pb = flowPos(B), x1 = pa.x + W, y1 = pa.y + H / 2, x2 = pb.x, y2 = pb.y + H / 2, m = flowCurveMid(x1, y1, x2, y2);
    const lit = !hi || (hi.has(a) && hi.has(b));
    edges.append(svg("path", { d: flowCurve(x1, y1, x2, y2), class: "fedge" + (lit ? "" : " dim") + (hi && lit ? " hi" : "") }));
    if (B.kind === "join") edges.append(svg("text", { x: x2 - 8, y: y2 + (flowIns(g, b).indexOf(a) ? 16 : -6), class: "fside", "text-anchor": "end" }, flowIns(g, b).indexOf(a) ? "오른쪽" : "왼쪽"));
    if (!o.readonly) {
      const mk = (dx, label, title, fn) => {
        const c = svg("g", { class: "fbtn", transform: `translate(${m.x + dx},${m.y})` }, svg("title", {}, title), svg("circle", { r: 9 }), svg("text", { "text-anchor": "middle", dy: "4" }, label));
        c.addEventListener("pointerdown", ev => ev.stopPropagation());
        c.addEventListener("click", ev => { ev.stopPropagation(); fn?.(a, b, ev); });
        return c;
      };
      edges.append(mk(-12, "+", "이 사이에 단계 끼우기", on.plus), mk(12, "×", "이음 끊기", on.cut));
    }
  });
  g.nodes.forEach(n => {
    const p = flowPos(n), mark = o.marks?.[n.id], lit = !hi || hi.has(n.id);
    const group = svg("g", { class: `fnode fk-${n.kind}${o.sel === n.id ? " sel" : ""}${lit ? "" : " dim"}${mark ? " mk-" + mark : ""}`, transform: `translate(${p.x},${p.y})`, "data-node": n.id },
      svg("title", {}, `${flowName(n.kind)} ${flowNodeText(n)}`.trim()), svg("rect", { width: W, height: H, rx: 12 }),
      svg("text", { x: 12, y: 21, class: "ft" }, flowName(n.kind) + (mark ? ({ add: " · 새로", chg: " · 바뀜", del: " · 빠짐" })[mark] : "")),
      svg("text", { x: 12, y: 41, class: "fs" }, flowClip(flowNodeText(n), 25)));
    if (o.rows?.[n.id] != null) group.append(svg("text", { x: W / 2, y: H + 13, "text-anchor": "middle", class: "frows" }, `${Number(o.rows[n.id]).toLocaleString()}행`));
    if (!o.readonly) {
      if (n.kind !== "table") group.append(svg("circle", { cx: 0, cy: H / 2, r: 6, class: "fport in", "data-in": n.id }));
      if (!FLOW.ENDS.includes(n.kind)) group.append(svg("circle", { cx: W, cy: H / 2, r: 6, class: "fport out", "data-out": n.id }));
      group.addEventListener("pointerdown", ev => { if (ev.target.dataset?.out) on.port?.(n.id, ev); else on.node?.(n.id, ev); });
    }
    nodes.append(group);
  });
  root.append(edges, nodes);
  return root;
}

// ---------- 화면 ----------
// FlowView(host, ctx): 흐름 보기. ctx = { tables(): 고를 수 있는 테이블 이름들, schema(): 지금 스키마 이름, edit: 단계를 고치는 칸을 놓을 요소, onChange(graph): 흐름이 바뀔 때 }
// 돌려주는 값: { set(graph), get(), body 쪽 변환은 부른 쪽이 한다 }
function FlowView(host, ctx) {
  let g = { nodes: [], edges: [] }, sel = null, hi = null, rows = {}, schemas = {}, schemaKey = "", schemaSeq = 0;
  const msg = el("div", { className: "fmsg" }), stage = el("div", { className: "fstage", tabIndex: 0 }), opt = el("div", { className: "fopt hidden" }), menu = el("div", { className: "fmenu hidden" });
  const say = (text, level = "") => { msg.textContent = text; msg.className = "fmsg " + level; };
  const palette = el("div", { className: "fpal" }, el("span", { className: "hint", textContent: "단계 놓기" }),
    ...FLOW_ADD.map(kind => el("button", { className: `btn fk-${kind}`, textContent: "+ " + flowName(kind), title: `${flowName(kind)} 단계를 놓습니다. 끌어서 위치를 옮기고, 오른쪽 점에서 다른 단계의 왼쪽 점으로 끌면 이어집니다.`, onclick: () => addNode(kind) })));
  const tools = el("div", { className: "row ftools" },
    el("button", { className: "btn", textContent: "단계별 행 수 세기", title: "단계마다 그 단계까지의 행 수를 셉니다(누를 때만). 원격 DB는 확인을 받습니다.", onclick: e => busy(e.currentTarget, countRows) }),
    el("button", { className: "btn", textContent: "최적화 보기", title: "집계가 결과인 흐름을 더 빠르게 만들 수 있는지 봅니다. 적용은 [이 흐름으로 바꾸기]를 누를 때만 합니다.", onclick: e => busy(e.currentTarget, () => optimize(false)) }),
    el("button", { className: "btn", textContent: "자동 정렬", onclick: () => { flowPlace(g); change(); } }),
    el("button", { className: "btn", textContent: "경로 밝힘 해제", onclick: () => { hi = null; draw(); } }));
  host.replaceChildren(palette, tools, msg, stage, opt, menu);

  const tableNames = () => ctx.tables?.() ?? [];
  async function loadSchemas(force = false) {  // 테이블 단계의 스키마(컬럼 종류). 테이블 이름들이 같으면 다시 받지 않는다.
    const key = JSON.stringify(g.nodes.filter(n => n.kind === "table").map(n => n.params?.table));
    if (!force && key === schemaKey) return schemas;
    if (!g.nodes.some(n => n.kind === "table" && n.params?.table)) { schemaSeq++; schemas = {}; schemaKey = key; return schemas; }
    const seq = ++schemaSeq;  // 테이블을 빨리 바꾸면 늦게 온 옛 응답이 새 스키마를 덮지 않게
    try { const got = (await api("/api/flow/schemas", { graph: g })).schemas; if (seq === schemaSeq) { schemas = got; schemaKey = key; } } catch (e) { if (seq === schemaSeq) say(e.message, "err"); }
    return schemas;
  }
  const colsOf = id => Object.entries(flowColumns(g, id, schemas));
  function change() { ctx.onChange?.(g); draw(); }
  function draw() {
    stage.replaceChildren(flowSvg(g, { sel, hi, rows, on: { node: nodeDown, port: portDown, plus: plusAt, cut: cutEdge } }));
    if (!g.nodes.length) stage.append(el("div", { className: "fempty hint", textContent: "단계가 없어요. 위에서 [+ 테이블]부터 놓으세요." }));
    editor();
  }
  const svgBox = () => stage.querySelector("svg").getBoundingClientRect();

  async function addNode(kind) {
    const base = flowNode(g, sel);
    const found = base ? colsOf(base.id) : [];
    const { graph, id } = flowAdd(g, kind, flowDefault(kind, { table: tableNames().includes(S.base) ? S.base : tableNames()[0], columns: found.map(c => c[0]), dates: found.filter(c => c[1] === "date").map(c => c[0]) }));
    flowNode(graph, id).pos = { x: FLOW.PAD, y: Math.max(FLOW.PAD, ...g.nodes.map(n => flowPos(n).y + FLOW.H + FLOW.GY)) };  // 맨 아래 빈 자리
    g = graph; sel = id; hi = null; say(`${flowName(kind)} 단계를 놓았어요. 오른쪽 점에서 끌어 다른 단계에 이으세요.`);
    await loadSchemas(); change();
  }

  // 단계 끌기 / 눌러 고르기
  function nodeDown(id, ev) {
    if (ev.button) return;
    const n = flowNode(g, id), start = { x: ev.clientX, y: ev.clientY }, from = { ...flowPos(n) };
    let moved = false;
    const move = e => {
      if (!moved && Math.hypot(e.clientX - start.x, e.clientY - start.y) < 4) return;
      moved = true;
      n.pos = { x: Math.max(0, from.x + e.clientX - start.x), y: Math.max(0, from.y + e.clientY - start.y) };
      stage.replaceChildren(flowSvg(g, { sel: id, hi, rows, on: { node: nodeDown, port: portDown, plus: plusAt, cut: cutEdge } }));
    };
    const up = () => {
      removeEventListener("pointermove", move); removeEventListener("pointerup", up);
      if (moved) { ctx.onChange?.(g); draw(); return; }
      sel = id;
      hi = ["aggregate", "metric", "chart", "card"].includes(n.kind) ? flowUpstream(g, id) : null;  // 집계·카드를 누르면 거기까지 오는 경로를 밝힌다
      if (hi) say(`${flowName(n.kind)}까지 오는 경로의 단계 ${hi.length}개를 밝혔어요.`);
      draw();
    };
    addEventListener("pointermove", move); addEventListener("pointerup", up);
  }
  // 오른쪽 점에서 끌어 다른 단계로 잇기
  function portDown(from, ev) {
    ev.stopPropagation();
    const box = svgBox(), A = flowNode(g, from), pa = flowPos(A), x1 = pa.x + FLOW.W, y1 = pa.y + FLOW.H / 2;
    const line = svg("path", { class: "fedge temp", d: flowCurve(x1, y1, x1, y1) });
    stage.querySelector("svg").append(line);
    const near = e => { const t = document.elementFromPoint(e.clientX, e.clientY)?.closest?.("[data-node]"); return t?.dataset.node; };
    const move = e => {
      const x = e.clientX - box.left, y = e.clientY - box.top, over = near(e);
      line.setAttribute("d", flowCurve(x1, y1, x, y));
      stage.querySelectorAll(".fnode").forEach(n => { n.classList.remove("ok", "no"); });
      if (over && over !== from) stage.querySelector(`[data-node="${over}"]`).classList.add(flowEdgeLocal(g, from, over) ? "no" : "ok");
    };
    const up = e => {
      removeEventListener("pointermove", move); removeEventListener("pointerup", up);
      const to = near(e);
      draw();
      if (to && to !== from) connect(from, to);
    };
    addEventListener("pointermove", move); addEventListener("pointerup", up);
  }
  // 이음: 서버가 막으면 이유 문장을 그대로 보이고 이어지지 않는다
  async function connect(a, b) {
    const local = flowEdgeLocal(g, a, b);
    if (local) return say(local, "err");
    await loadSchemas();
    let found;
    try { found = await api("/api/flow/check_edge", { graph: g, a, b, schemas }); } catch (e) { return say(e.message, "err"); }
    if (!found.ok) return say(found.reason + (found.suggest ? " " + (found.suggest.text ?? "") : ""), "err");
    g = { ...flowCopy(g), edges: [...g.edges, [a, b]] };
    hi = null; sel = b; say(`${flowName(flowNode(g, a).kind)} → ${flowName(flowNode(g, b).kind)} 이음을 만들었어요.`, "ok");
    await loadSchemas(true); change();
    if (flowNode(g, b).kind === "join" && flowIns(g, b).length === 2) await suggestKeys(b);
  }
  function cutEdge(a, b) { g = flowCut(g, a, b); hi = null; say("이음을 끊었어요."); change(); }
  // 이음 위 [+]: 끼울 수 있는 단계 종류를 보이고, 고르면 끼운 뒤 서버 검사로 안 되면 되돌린다
  function plusAt(a, b, ev) {
    const kinds = flowInsertKinds(g, a, b), box = host.getBoundingClientRect();
    menu.replaceChildren(...(kinds.length ? kinds.map(k => el("button", { className: `btn fk-${k}`, textContent: flowName(k), onclick: () => { menu.classList.add("hidden"); insertNode(a, b, k); } }))
      : [el("span", { className: "hint", textContent: "여기에는 끼울 수 있는 단계가 없어요(순서 때문에)." })]), el("button", { className: "ghost", textContent: "닫기", onclick: () => menu.classList.add("hidden") }));
    menu.style.left = Math.max(0, ev.clientX - box.left - 20) + "px"; menu.style.top = (ev.clientY - box.top + 12) + "px";
    menu.classList.remove("hidden");
  }
  async function insertNode(a, b, kind) {
    const have = colsOf(a);
    const { graph, id } = flowInsert(g, a, b, kind, flowDefault(kind, { columns: have.map(c => c[0]), dates: have.filter(c => c[1] === "date").map(c => c[0]) }));
    await loadSchemas();
    let found;
    try { found = await api("/api/flow/check", { graph, schemas }); } catch (e) { return say(e.message, "err"); }
    if (found.problems.length) return say("끼울 수 없어요: " + found.problems[0].reason, "err");
    g = graph; sel = id; hi = null; say(`${flowName(kind)} 단계를 끼웠어요. 오른쪽(또는 아래) 칸에서 내용을 고치세요.`, "ok"); change();
  }

  // 조인 키 찾기: 결정적이면 자동으로 고르고, 아니면 상위 3개를 근거와 함께 고르게 한다
  async function suggestKeys(joinId) {
    const [l, r] = flowIns(g, joinId).map(i => flowRoot(g, i));
    if (!l || !r) return;
    const pick = x => ({ table: x.params.table, ...(x.params.schema ? { schema: x.params.schema } : {}) });
    let d;
    try { d = await api("/api/flow/keys", { a: pick(l), b: pick(r) }); } catch (e) { return say("공통 키를 찾지 못했어요: " + e.message, "err"); }
    const same = () => flowNode(g, joinId) && flowIns(g, joinId).map(i => flowRoot(g, i)?.id).join() === [l.id, r.id].join();  // 기다리는 사이 조인을 지우거나 이음을 바꿨으면 옛 테이블 쌍의 키를 넣지 않는다(단계를 고치면 그래프를 통째 복사하니 id로 견준다)
    if (!same()) return;
    const use = async cand => {
      if (!same()) { opt.classList.add("hidden"); return say("그 사이 조인이 바뀌어 키를 넣지 않았어요. 공통 키를 다시 찾아 주세요.", "err"); }
      flowNode(g, joinId).params.keys = cand.keys;
      try { await api("/api/flow/keys/remember", { source_key: d.source_key ?? "", a: l.params.table, b: r.params.table, fingerprint: d.fingerprint, keys: cand.keys }); } catch { /* 기억하지 못해도 흐름은 쓸 수 있다 */ }
      opt.classList.add("hidden"); change();
      say(`공통 키를 ${cand.keys.map(k => `${k[0]} = ${k[1]}`).join(", ")}로 정했어요(${cand.basis}). 이은 뒤 행이 늘어나는지는 [조인 부풂 확인]으로 볼 수 있어요.`, "ok");
    };
    if (d.decision === "auto") { await use(d.candidates[0]); return; }
    opt.classList.remove("hidden");
    opt.replaceChildren(el("h3", { textContent: d.decision === "none" ? "공통 키 후보가 없어요" : "어느 키로 이을까요?" }), el("div", { className: "hint", textContent: d.note || "" }),
      ...d.candidates.map(c => el("div", { className: "fkey" }, el("b", { textContent: c.keys.map(k => `${k[0]} = ${k[1]}`).join(", ") }), el("span", { className: "hint", textContent: `점수 ${c.score} · ${c.basis}` }),
        el("ul", {}, ...c.reasons.map(t => el("li", { textContent: t }))), el("button", { className: "btn p", textContent: "이 키로", onclick: () => use(c) }))),
      d.decision === "none" ? el("div", { className: "hint", textContent: d.date ? `날짜 컬럼 ${d.date.a}와 ${d.date.b}를 ${d.date.units.join("·")} 단위로 맞춰 볼 수 있어요. 조인 단계에서 직접 키를 넣으세요.` : "조인 단계에서 직접 키를 넣으세요." }) : "",
      el("button", { className: "ghost", textContent: "닫기", onclick: () => opt.classList.add("hidden") }));
  }
  async function fanout(joinId) {
    try {
      const d = await api("/api/flow/fanout", { graph: g, join: joinId });
      if (!d.checked && d.note && await confirmScan({ text: d.note })) return fanoutAgain(joinId);
      say(d.checked ? (d.text || "오른쪽 키가 고유해서 행이 늘지 않아요.") : d.note, d.level === "bad" ? "err" : d.level === "warn" ? "warn" : "ok");
    } catch (e) { say(e.message, "err"); }
  }
  async function fanoutAgain(joinId) {
    try { const d = await api("/api/flow/fanout", { graph: g, join: joinId, scan_ok: true }); say(d.text || "오른쪽 키가 고유해서 행이 늘지 않아요.", d.level === "bad" ? "err" : d.level === "warn" ? "warn" : "ok"); } catch (e) { say(e.message, "err"); }
  }

  // [단계별 행 수 세기]
  async function countRows() {
    if (!g.nodes.length) return say("단계가 없어요.", "err");
    const target = flowBodyTarget(g);
    try {
      let d = await api("/api/flow/rows", { graph: g, target });
      if (d.needs_confirm) {
        if (!await confirmScan({ text: d.note })) return say("세지 않았어요.");
        d = await api("/api/flow/rows", { graph: g, target, confirmed: true });
      }
      rows = Object.fromEntries(d.steps.map(s => [s.id, s.rows]));
      say(`${d.steps.length}개 단계의 행 수를 셌어요.`, "ok"); draw();
    } catch (e) { say(e.message, "err"); }
  }

  // [최적화 보기]: 원래와 바뀐 흐름을 나란히, 바뀐 곳과 이유, 결과 같음 확인 여부
  async function optimize(scanOk) {
    if (!g.nodes.length) return say("단계가 없어요.", "err");
    let d;
    try { d = await api("/api/flow/optimize", { graph: g, scan_ok: scanOk }); } catch (e) { return say(e.message, "err"); }
    opt.classList.remove("hidden");
    if (!d.ok) { opt.replaceChildren(el("div", { className: "hint", textContent: d.reason }), el("button", { className: "ghost", textContent: "닫기", onclick: () => opt.classList.add("hidden") })); return; }
    const marks = flowDiff(g, d.graph), usable = d.rules.filter(r => ["kept", "unverified"].includes(r.status));
    const checks = Object.fromEntries(usable.map(r => [r.no, el("input", { type: "checkbox", checked: true, style: "width:auto" })]));
    const STATUS = { kept: "적용 가능 · 결과 같음 확인", unverified: "적용 가능 · 확인 안 됨", dropped: "뺌 · 결과가 달라요", skipped: "바꿀 것이 없어요" };
    const sure = d.verified && usable.every(r => r.equal === true);
    const badge = el("span", { className: "fbadge " + (sure ? "ok" : "warn"), textContent: sure ? "결과 같음 확인됨" : "확인 안 됨" });
    const rules = d.rules.map(r => el("div", { className: "frule " + r.status },
      el("label", {}, checks[r.no] ?? "", el("b", { textContent: ` ${r.no}. ${r.title}` }), el("span", { className: "hint", textContent: ` · ${STATUS[r.status]}` })),
      ...(r.items ?? []).map(i => el("div", { className: "hint", textContent: "· " + i.text })), ...(r.refused ?? []).map(i => el("div", { className: "hint", textContent: "× " + i.text })), r.why ? el("div", { className: "hint err", textContent: r.why }) : ""));
    const apply = el("button", { className: "btn p", textContent: "이 흐름으로 바꾸기", disabled: !usable.length, onclick: e => busy(e.currentTarget, async () => {
      try {
        const done = await api("/api/flow/optimize/apply", { graph: g, rules: usable.filter(r => checks[r.no].checked).map(r => r.no), scan_ok: scanOk });
        g = flowPlace(done.graph); sel = null; hi = null; rows = {}; opt.classList.add("hidden");
        say(`바꿨어요(규칙 ${done.applied.join(", ")}번).` + (done.dropped.length ? " 빠진 것: " + done.dropped.map(x => `${x.no}번 ${x.why}`).join(" ") : "") + (done.body_problem ? ` 단계 카드로는 나타낼 수 없어요: ${done.body_problem}` : ""), "ok");
        await loadSchemas(true); change();
      } catch (er) { say(er.message, "err"); }
    }) });
    const after = flowPlace(flowCopy(d.graph)), before = flowPlace(flowCopy(g));
    opt.replaceChildren(el("div", { className: "row" }, el("h3", { textContent: "최적화 제안", style: "margin:0" }), badge, el("span", { className: "hint", textContent: d.bytes?.text ?? "" })),
      d.note ? el("div", { className: "hint", textContent: d.note }) : "",
      ...d.warnings.map(w => el("div", { className: "fwarn", textContent: typeof w === "string" ? w : w.text ?? JSON.stringify(w) })),
      el("div", { className: "fcompare" }, el("div", {}, el("div", { className: "hint", textContent: "원래" }), el("div", { className: "fmini" }, flowSvg(before, { readonly: true, scale: .6, marks: Object.fromEntries(Object.entries(marks).filter(([, v]) => v === "del" || v === "chg")) }))),
        el("div", {}, el("div", { className: "hint", textContent: "바꾼 뒤" }), el("div", { className: "fmini" }, flowSvg(after, { readonly: true, scale: .6, marks })))),
      ...rules, el("div", { className: "row" }, apply, !d.verified && d.remote ? el("button", { className: "btn", textContent: "전체를 읽어 결과 확인", onclick: e => busy(e.currentTarget, async () => { if (await confirmScan({ text: d.note })) await optimize(true); }) }) : "",
        el("button", { className: "ghost", textContent: "닫기", onclick: () => opt.classList.add("hidden") })));
  }

  // ---------- 단계 고치기 (단계 카드와 같은 입력) ----------
  const field = (label, input) => el("label", { className: "fld" }, el("span", { className: "hint", textContent: label }), input);
  const pick = (options, value, on, blank) => {
    const s = el("select", {}, ...(blank != null ? [el("option", { value: "", textContent: blank })] : []), ...options.map(o => el("option", { value: o, textContent: o })));
    s.value = value ?? ""; s.onchange = () => on(s.value); return s;
  };
  const text = (value, on, placeholder = "") => { const i = el("input", { value: value ?? "", placeholder }); i.onchange = () => on(i.value); return i; };
  const setParams = (n, patch) => { n.params = { ...n.params, ...patch }; ctx.onChange?.(g); const wasTable = n.kind === "table"; if (wasTable) loadSchemas(true).then(draw); else draw(); };
  function editor() {
    const box = ctx.edit;
    if (!box) return;
    const n = flowNode(g, sel);
    if (!n) { box.replaceChildren(el("div", { className: "hint", textContent: "단계를 누르면 여기서 고칠 수 있어요." })); return; }
    const p = n.params ?? {}, ins = flowIns(g, n.id), cols = Object.keys(flowColumns(g, ins[0], schemas)), kids = [el("h2", { textContent: `${flowName(n.kind)} 단계` })];
    const del = el("button", { className: "ghost err", textContent: "이 단계 지우기", onclick: () => { g = flowRemove(g, n.id); sel = null; hi = null; say("단계를 지웠어요."); change(); } });
    if (n.kind === "table") kids.push(field("테이블", pick(tableNames(), p.table, v => setParams(n, { table: v }), "테이블 고르기")));
    else if (n.kind === "union") kids.push(field("이어붙이는 방식", pick(SERVER.flow.unions, p.how, v => setParams(n, { how: v }))), el("div", { className: "hint", textContent: "이을 테이블들을 이 단계에 이으세요(첫 이음이 기준 테이블)." }));
    else if (n.kind === "join") {
      const left = Object.keys(flowColumns(g, ins[0], schemas)), right = Object.keys(flowColumns(g, ins[1], schemas)), keys = p.keys ?? [];
      kids.push(field("방식", pick(SERVER.flow.joins, p.how, v => setParams(n, { how: v }))), ...keys.map((k, i) => el("div", { className: "row" },
        pick(left, k[0], v => { keys[i] = [v, k[1]]; setParams(n, { keys }); }, "왼쪽 키"), el("span", { textContent: p.how === "asof" && !i ? "≥" : "=" }), pick(right, k[1], v => { keys[i] = [k[0], v]; setParams(n, { keys }); }, "오른쪽 키"),
        el("button", { className: "ghost", textContent: "×", onclick: () => setParams(n, { keys: keys.filter((_, j) => j !== i) }) }))),
      el("div", { className: "row" }, el("button", { className: "btn", textContent: "+ 키 추가", onclick: () => setParams(n, { keys: [...keys, ["", ""]] }) }),
        ins.length === 2 ? el("button", { className: "btn", textContent: "공통 키 찾기", onclick: e => busy(e.currentTarget, () => suggestKeys(n.id)) }) : "",
        ins.length === 2 ? el("button", { className: "btn", textContent: "조인 부풂 확인", title: "이은 뒤 행이 늘어나는지 셉니다(전체를 읽음).", onclick: e => busy(e.currentTarget, () => fanout(n.id)) }) : ""));
    } else if (n.kind === "period") {
      kids.push(el("div", { className: "hint", textContent: "날짜컬럼 2025-07-01 ~ 2025-09-30 (한쪽 비워도 됨)" }), field("기간 줄", text(p.line ?? flowNodeText(n), v => setParams(n, { line: v }))),
        field("날짜 컬럼 넣기", pick(cols, "", v => v && setParams(n, { line: `${v} ${(p.line ?? "").replace(/^\S+\s*/, "")}`.trim() }), "컬럼 고르기")));
    } else if (n.kind === "condition") kids.push(conditionEditor(n, cols));
    else if (n.kind === "calc") kids.push(el("div", { className: "hint", textContent: document.getElementById("calc-guide")?.textContent ?? "" }), field("계산 줄", text(p.line, v => setParams(n, { line: v }), "예: 식 금액 * 수량 이름 합계")));
    else if (n.kind === "dedupe") kids.push(field("같은 값이면 하나만 남길 컬럼(쉼표로)", text((p.keys ?? []).join(", "), v => setParams(n, { keys: v.split(",").map(s => s.trim()).filter(Boolean) }))), field("남길 행의 정렬 기준", pick(cols, p.order, v => setParams(n, { order: v }), "정렬 기준 없음")));
    else if (n.kind === "aggregate") kids.push(aggregateEditor(n, cols));
    else if (n.kind === "metric") kids.push(field("지표 이름(쉼표로)", text((p.names ?? []).join(", "), v => setParams(n, { names: v.split(",").map(s => s.trim()).filter(Boolean) }))), field("나눌 컬럼", pick(cols, [p.by].flat()[0], v => setParams(n, { by: v ? [v] : undefined }), "나누지 않음")));
    else kids.push(field("X 컬럼", pick(cols, p.x, v => setParams(n, { x: v }), "고르기")), field("나눌 컬럼", pick(cols, p.split, v => setParams(n, { split: v || undefined }), "나누지 않음")));
    kids.push(del);
    box.replaceChildren(...kids);
  }
  // 조건 단계: 단계 카드의 칸 방식(컬럼·연산자·값)과 한 줄 입력
  function conditionEditor(n, cols) {
    const ops = () => (flowColumns(g, flowIns(g, n.id)[0], schemas)[col.value] === "text" ? OPS_TEXT : OPS_OTHER);
    const col = pick(cols, cols[0], () => fillOps()), op = el("select"), v1 = el("input", { placeholder: "값" }), v2 = el("input", { placeholder: "값2", className: "hidden" });
    const fillOps = () => { const was = op.value; fill(op, ops()); op.value = ops().includes(was) ? was : "="; v1.classList.toggle("hidden", op.value.endsWith("null")); v2.classList.toggle("hidden", op.value !== "between"); };
    op.onchange = fillOps; fillOps();
    const line = () => op.value.endsWith("null") ? `${col.value} ${op.value}` : op.value === "between" ? `${col.value} between ${v1.value.trim()} ${v2.value.trim()}` : `${col.value} ${op.value} ${v1.value.trim()}`;
    return el("div", {}, el("div", { className: "hint", textContent: "컬럼 >= 값 / 컬럼 like 값 / 컬럼 starts 값 / 컬럼 ends 값 / 컬럼 in 값1, 값2 / 컬럼 is [not] null / 조건 or 조건" }),
      field("조건 줄", text(condText(n.params?.cond), v => setParams(n, { cond: v }))), el("div", { className: "row" }, col, op), el("div", { className: "row" }, v1, v2),
      el("button", { className: "btn", textContent: "칸으로 만든 조건 넣기", onclick: () => { if (v1.value.trim() || op.value.endsWith("null")) setParams(n, { cond: line() }); } }));
  }
  // 집계 단계: 묶을 컬럼과 계열들(함수 사전의 집계 고르기)
  function aggregateEditor(n, cols) {
    const p = n.params, series = p.series ?? [], pairs = cols.map(c => [c, flowColumns(g, flowIns(g, n.id)[0], schemas)[c] ?? "other"]);
    const ids = Funcs.cat ? Funcs.cat.screens.chart.filter(i => Funcs.cat.byId[i].aggregate) : [];
    const rowsEl = series.map((s, i) => {
      const picker = Funcs.cat ? Funcs.picker({ ids, columns: () => pairs }) : null, y = pick(cols, s.y, v => { series[i] = { ...series[i], y: v }; setParams(n, { series }); }, "값 컬럼");
      if (picker) { picker.set({ agg: s.agg, opts: s.opts }); picker.select.addEventListener("change", () => { series[i] = { ...series[i], ...picker.get() }; setParams(n, { series }); }); }
      return el("div", { className: "fser" }, picker?.el ?? el("span", { textContent: s.agg }), y, text(s.name ?? "", v => { series[i] = { ...series[i], name: v || undefined }; setParams(n, { series }); }, "이름"),
        el("button", { className: "ghost", textContent: "×", onclick: () => setParams(n, { series: series.filter((_, j) => j !== i) }) }));
    });
    return el("div", {}, field("묶을 컬럼", el("div", { className: "chips open" }, ...(p.by ?? []).map(c => chip(c, () => setParams(n, { by: p.by.filter(x => x !== c) }))), pick(cols.filter(c => !(p.by ?? []).includes(c)), "", v => v && setParams(n, { by: [...(p.by ?? []), v] }), "+ 컬럼"))),
      el("div", { className: "hint", textContent: "계열(집계 · 값 컬럼 · 이름)" }), ...rowsEl, el("button", { className: "btn", textContent: "+ 계열", onclick: () => setParams(n, { series: [...series, { agg: "count", y: "", name: "" }] }) }));
  }

  stage.addEventListener("keydown", e => { if (e.key === "Delete" && sel) { g = flowRemove(g, sel); sel = null; hi = null; change(); } });
  document.addEventListener("pointerdown", e => { if (!e.target.closest(".fmenu, .fbtn")) menu.classList.add("hidden"); });
  return {
    get: () => g,
    selected: () => sel,
    async set(graph, { layout = true } = {}) { g = layout ? flowPlace(flowCopy(graph)) : flowCopy(graph); sel = null; hi = null; rows = {}; opt.classList.add("hidden"); await loadSchemas(true); draw(); },
    say,
  };
}
