// 대시보드 격자 배치의 계산(화면 요소 없이): 12칸 × 행 높이 40px의 격자, 카드 이동·크기 바꾸기, 세로 압축. 끌기·손잡이는 dash_edit.js가 이 계산을 부른다(node 테스트가 본다).
// 카드 자리(layout) = {x, y, w, h}: x는 칸(0~11), y는 행, w·h는 2~12. 서버(dash_store.check_layout)와 같은 범위다.
const GRID_COLS = 12, GRID_ROW = 40, GRID_GAP = 10, GRID_MIN = 2, GRID_MAX = 12;

const toInt = (v, fallback) => (Number.isFinite(+v) ? Math.round(+v) : fallback);
const clampInt = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

// 범위 안으로 맞춘 자리: w·h는 2~12, x는 0 ~ 12 - w, y는 0 이상(999까지)
function clampLayout(l) {
  const w = clampInt(toInt(l?.w, 6), GRID_MIN, GRID_MAX), h = clampInt(toInt(l?.h, 6), GRID_MIN, GRID_MAX);
  return { x: clampInt(toInt(l?.x, 0), 0, GRID_COLS - w), y: clampInt(toInt(l?.y, 0), 0, 999), w, h };
}

// 두 자리가 겹치는지(맞닿기만 하면 겹침이 아니다)
const overlaps = (a, b) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;

// 세로 압축: 움직인 카드(movedId)를 먼저 놓고, 나머지는 (y, x) 순으로 돌며 이미 놓인 카드와 겹치지 않는 가장 작은 y에 놓는다(x는 그대로).
// items: [{id, layout}]. 돌려주는 값: {id: 새 자리}. movedId가 없으면 모두 (y, x) 순으로 위로 붙인다.
function compactLayout(items, movedId) {
  const placed = [], out = {};
  const put = (id, l) => { out[id] = l; placed.push(l); };
  const moved = items.find(i => i.id === movedId);
  if (moved) put(moved.id, clampLayout(moved.layout));
  const rest = items.filter(i => i !== moved).map(i => ({ id: i.id, layout: clampLayout(i.layout) })).sort((a, b) => a.layout.y - b.layout.y || a.layout.x - b.layout.x);
  for (const { id, layout } of rest) {
    let y = 0;
    while (placed.some(p => overlaps({ ...layout, y }, p))) {
      y = Math.min(...placed.filter(p => overlaps({ ...layout, y }, p)).map(p => p.y + p.h));  // 겹치는 카드의 바로 아래로 건너뛴다
    }
    put(id, { ...layout, y });
  }
  return out;
}

// 포인터가 가리키는 칸: 격자 왼쪽 위(left, top)에서 칸 간격(col·row는 칸 하나 + 간격의 길이)으로 나눠 반올림한다. 카드 안에서 잡은 자리(grabX·grabY)만큼은 뺀다.
function cellAt(px, py, { left, top, col, row }, grabX = 0, grabY = 0) {
  return { x: Math.round((px - left - grabX) / col), y: Math.round((py - top - grabY) / row) };
}

// 카드를 cell 자리로 옮긴 뒤의 모든 카드 자리
function moveCard(items, id, cell) {
  const item = items.find(i => i.id === id);
  if (!item) return compactLayout(items);
  const once = compactLayout(items.map(i => (i === item ? { id, layout: { ...i.layout, x: cell.x, y: cell.y } } : i)), id);
  return compactLayout(items.map(i => ({ id: i.id, layout: once[i.id] })));  // 놓은 카드도 위로 붙인다(빈 아래쪽에 뜨지 않게, docs: 빈 곳은 위로 메워진다)
}

// 카드 크기를 w·h(2~12)로 바꾼 뒤의 모든 카드 자리. 오른쪽 끝을 넘으면 x를 당긴다.
function resizeCard(items, id, w, h) {
  const item = items.find(i => i.id === id);
  if (!item) return compactLayout(items);
  const next = clampLayout({ ...item.layout, w, h });
  return compactLayout(items.map(i => (i === item ? { id, layout: next } : i)), id);
}

// 손잡이를 끌 때의 새 크기: 포인터가 카드 왼쪽 위에서 얼마나 떨어졌는지(dx, dy 픽셀)를 칸으로
function sizeAt(dx, dy, { col, row, gap = GRID_GAP }) {
  return { w: clampInt(Math.round((dx + gap) / col), GRID_MIN, GRID_MAX), h: clampInt(Math.round((dy + gap) / row), GRID_MIN, GRID_MAX) };
}

// 새 카드를 놓을 자리: 맨 아래(왼쪽)
function nextLayout(items, w = 6, h = 8) {
  return { x: 0, y: Math.min(999, Math.max(0, ...items.map(i => i.layout.y + i.layout.h))), w, h };
}
