// 흐름(퍼널)의 그림과 표: 분석 화면(prep.html)과 대시보드의 funnel 카드가 함께 쓴다. 계산은 서버(funnel.py)가 하고 여기서는 받은 단계별 사람 수만 그린다.
// 생키: 단계마다 세로 막대(높이 ∝ 사람 수), 다음 단계로 간 사람은 초록 띠, 빠진 사람은 주황 띠가 막대 오른쪽에서 아래로 갈라져 내려간다.
// 띠는 3차 베지어 두 개(위쪽 가장자리, 아래쪽 가장자리)와 직선 둘로 닫은 경로다. sankeyLayout은 화면 없이 좌표만 구한다(node 테스트가 본다).
const FUNNEL_COLORS = { bar: "#00968f", next: "#008300", drop: "#eb6834" };

// steps: [{name, users, conv, from_first, drop}]. 돌려주는 값: {width, height, bars[{x, y, w, h, name, users}], nexts[{path, from, to, users}], drops[{path, step, users}]}
function sankeyLayout(steps, { width = 720, height = 340, pad = 12, barW = 22, top = 30, bottom = 40, lane = 14 } = {}) {
  const k = steps.length, users = steps.map(s => s.users), first = users[0] || 0;
  const drops = users.map((n, i) => (i ? Math.max(0, users[i - 1] - n) : 0)), worst = Math.max(0, ...drops);
  const avail = height - top - bottom - lane, per = first + worst ? avail / (first + worst) : 0;  // 사람 한 명의 높이: 첫 막대와 가장 큰 이탈 띠가 세로에 다 들어가게
  const h = n => (n > 0 ? Math.max(1, n * per) : 0);
  const gap = k > 1 ? (width - 2 * pad - barW) / (k - 1) : 0, x = i => (k > 1 ? pad + i * gap : (width - barW) / 2);
  const bars = steps.map((s, i) => ({ x: x(i), y: top, w: barW, h: h(s.users), name: s.name, users: s.users }));
  const band = (x0, y0a, y0b, x1, y1a, y1b) => {  // 위 가장자리 (x0,y0a)→(x1,y1a), 아래 가장자리 (x1,y1b)→(x0,y0b): 곡선 둘 + 직선 둘
    const mid = (x0 + x1) / 2;
    return `M${x0},${y0a} C${mid},${y0a} ${mid},${y1a} ${x1},${y1a} L${x1},${y1b} C${mid},${y1b} ${mid},${y0b} ${x0},${y0b} Z`;
  };
  const nexts = [], lost = [], laneTop = top + h(first) + lane;
  for (let i = 1; i < k; i++) {
    const prev = bars[i - 1], cur = bars[i], hn = h(users[i]), hd = h(drops[i]), x0 = prev.x + barW;
    if (users[i] > 0) nexts.push({ path: band(x0, top, top + hn, cur.x, top, top + hn), from: i - 1, to: i, users: users[i] });
    if (drops[i] > 0) {  // 이탈: 이전 막대의 남는 아랫부분에서 시작해 두 막대 사이의 아래 칸으로 내려간다
      const xe = x0 + (cur.x - x0) * 0.72, y0 = top + hn;
      lost.push({ path: band(x0, y0, y0 + hd, xe, laneTop, laneTop + hd), step: i, users: drops[i] });
    }
  }
  return { width, height, bars, nexts, drops: lost, per };
}

const fpct = v => (v == null ? "-" : `${(v * 100).toFixed(1)}%`);

// 생키 SVG 요소. colors = {bar, next, drop, text, line}(없으면 기본). 막대 위에 단계 이름, 아래에 사람 수와 처음 대비 비율.
function sankeySvg(steps, colors = {}, size = {}) {
  const c = { ...FUNNEL_COLORS, text: "currentColor", ...colors }, g = sankeyLayout(steps, size);
  const label = (x, y, text, anchor, extra = {}) => svg("text", { x, y, "text-anchor": anchor, fill: c.text, "font-size": 11, ...extra }, text);
  const short = s => (s.length > 14 ? s.slice(0, 13) + "…" : s);
  const kids = [
    ...g.nexts.map(b => svg("path", { d: b.path, fill: c.next, opacity: 0.45 }, svg("title", {}, `${steps[b.from].name} → ${steps[b.to].name}: ${b.users.toLocaleString()}명`))),
    ...g.drops.map(b => svg("path", { d: b.path, fill: c.drop, opacity: 0.55 }, svg("title", {}, `${steps[b.step - 1].name}에서 ${b.users.toLocaleString()}명 이탈`))),
    ...g.bars.map(b => svg("rect", { x: b.x, y: b.y, width: b.w, height: Math.max(b.h, 1), fill: c.bar, rx: 2 }, svg("title", {}, `${b.name}: ${b.users.toLocaleString()}명`))),
    ...g.bars.map((b, i) => {
      const anchor = i === 0 ? "start" : i === g.bars.length - 1 ? "end" : "middle", lx = anchor === "start" ? b.x : anchor === "end" ? b.x + b.w : b.x + b.w / 2;
      return svg("g", {}, label(lx, 16, short(b.name), anchor, { "font-weight": 600 }), label(lx, g.height - 22, `${b.users.toLocaleString()}명`, anchor), label(lx, g.height - 8, i ? fpct(steps[i].from_first) : "100%", anchor, { opacity: 0.7 }));
    })];
  return svg("svg", { viewBox: `0 0 ${g.width} ${g.height}`, preserveAspectRatio: "xMidYMid meet", style: "width:100%;height:100%", role: "img" }, ...kids);
}

// 표: 단계별 수, 다음 단계 전환율(이 단계 → 다음 단계), 처음 대비 비율, 이탈 수(이 단계에서 다음 단계로 못 간 사람). 마지막 단계는 다음이 없다.
function funnelRows(steps) {
  return steps.map((s, i) => {
    const next = steps[i + 1];
    return { 단계: s.name, "사용자 수": s.users, "다음 단계 전환율": next ? fpct(next.conv) : "-", "처음 대비": fpct(s.from_first), "이탈 수": next ? next.drop : "-" };
  });
}
const FUNNEL_COLUMNS = ["단계", "사용자 수", "다음 단계 전환율", "처음 대비", "이탈 수"];

// 한 줄 설명: 처음 단계에서 끝 단계까지 남은 비율과 가장 많이 빠진 곳. 지어내지 않고 숫자가 없으면 그 말을 뺀다.
function funnelSentence(steps) {
  if (!steps.length || !steps[0].users) return "첫 단계에 도달한 사용자가 없어요.";
  const first = steps[0], last = steps[steps.length - 1];
  if (steps.length === 1) return `${first.name}에 ${first.users.toLocaleString()}명이 있어요.`;
  const worst = steps.slice(1).reduce((a, s, i) => (s.drop > (a?.drop ?? -1) ? { ...s, from: steps[i].name } : a), null);
  return `${first.name} ${first.users.toLocaleString()}명 중 ${last.users.toLocaleString()}명(${fpct(last.from_first)})이 ${last.name}까지 갔어요.` +
    (worst && worst.drop > 0 ? ` 가장 많이 빠진 곳은 ${worst.from} → ${worst.name}로, ${worst.drop.toLocaleString()}명(${fpct(worst.drop / steps.find(s => s.name === worst.from).users)})이 다음 단계로 가지 못했어요.` : " 중간에 빠진 사람은 없어요.");
}

// 기간 제한 글: {n, unit} → "30분 안에"
const WINDOW_UNIT_NAMES = { minute: "분", hour: "시간", day: "일" };
const windowText = w => (w && w.n ? `첫 단계부터 ${w.n}${WINDOW_UNIT_NAMES[w.unit] ?? ""} 안에 도달한 사람만` : "기간 제한 없음");
