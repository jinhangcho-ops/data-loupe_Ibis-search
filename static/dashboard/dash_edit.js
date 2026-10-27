// 대시보드 편집: 12칸 격자에서 제목 줄을 끌어 카드를 옮기고 오른쪽 아래 손잡이로 크기를 바꾼다(계산은 dash_grid.js). 카드 자리는 layout에 저장한다.
// 북극성·카운터 지표 표시(★·⚑)와 카운터가 한계를 벗어났는지 보는 counterAlerts도 여기 있다. board.js, dash.js, dash_cards.js, dash_grid.js가 먼저 불려 있어야 한다.

// 카드가 다시 구할 기록(source)이 비어 있는 빈 틀인지(템플릿·카드 모음에서 막 놓은 카드)
const SHELL_NOTE = "데이터를 정하세요. 카드의 ⚙를 눌러 [데이터]에서 테이블과 컬럼을 고르세요.";
const isShell = card => !(card.source && (card.source.body || card.source.recipe || card.source.metric_id || card.source.flow_id));

// 카운터 지표(북극성을 올리다 나빠지면 안 되는 지표)가 한계를 벗어났는지. star = {card, counters: [카드 id], limits: {카드 id: {op: ">="|"<=", value}}}.
// 돌려주는 값: [{id, title, value, op, limit, text}]. 값이 없거나 한계를 정하지 않은 카운터는 뺀다.
function counterAlerts(cards, star) {
  const out = [];
  for (const id of star?.counters ?? []) {
    const card = cards.find(c => c.id === id), lim = star.limits?.[id];
    if (!card || !lim) continue;
    const v = cardValue(card, 0);
    if (v == null || !Number.isFinite(v)) continue;
    const bad = lim.op === ">=" ? v < lim.value : v > lim.value, fmt = cardFormat(card);
    if (bad) out.push({ id, title: card.title, value: v, op: lim.op, limit: lim.value,
      text: `${card.title}: 지금 값 ${showValue(v, fmt)}이(가) 한계(${showValue(lim.value, fmt)} ${lim.op === ">=" ? "이상" : "이하"})를 벗어났어요` });
  }
  return out;
}

// 지금 있는 카드만 남긴 북극성·카운터(지운 카드의 id가 남으면 서버가 저장을 거부한다)
function liveStar(star, cards) {
  const ids = new Set(cards.map(c => c.id)), counters = (star?.counters ?? []).filter(id => ids.has(id));
  return { card: ids.has(star?.card) ? star.card : "", counters, limits: Object.fromEntries(Object.entries(star?.limits ?? {}).filter(([id]) => counters.includes(id))) };
}

// 격자 카드 모음: DashBoard에 끌어 옮기기·손잡이 크기 바꾸기·설정 열기(⚙)·북극성 배지를 더한다.
// options: DashBoard의 것 + onEdit(card)(⚙를 누르면). star는 화면이 정해 둔다(북극성·카운터).
class EditBoard extends DashBoard {
  constructor(grid, options) {
    super(grid, options);
    Object.assign(this, { onEdit: options.onEdit, star: { card: "", counters: [], limits: {} }, editing: null });
    grid.classList.add("grid");
  }
  // 빈 틀 카드는 안내를 보인다
  wrap(card) {
    const wrapped = super.wrap(card);
    if (isShell(wrapped)) wrapped.note = SHELL_NOTE;
    else if (wrapped.note === SHELL_NOTE) delete wrapped.note;
    return wrapped;
  }
  // 격자 한 칸의 크기: left·top(격자 안쪽 왼쪽 위), col·row(칸 하나 + 간격의 길이), gap
  metrics() {
    const rect = this.grid.getBoundingClientRect(), css = getComputedStyle(this.grid);
    const padL = parseFloat(css.paddingLeft) || 0, padR = parseFloat(css.paddingRight) || 0, gap = parseFloat(css.columnGap) || GRID_GAP;
    const colW = (rect.width - padL - padR - gap * (GRID_COLS - 1)) / GRID_COLS;
    return { left: rect.left + padL, top: rect.top + (parseFloat(css.paddingTop) || 0), col: colW + gap, row: GRID_ROW + gap, gap };
  }
  place(box, l) {
    box.style.gridColumn = `${l.x + 1} / span ${l.w}`;
    box.style.gridRow = `${l.y + 1} / span ${l.h}`;
  }
  items() { return this.cards.map(c => ({ id: c.id, layout: clampLayout(c.layout) })); }
  // 새 자리들을 화면에 바로 보인다(저장은 commit)
  preview(layouts) {
    this.grid.querySelectorAll(".bcard[data-id]").forEach(b => { if (layouts[b.dataset.id]) this.place(b, layouts[b.dataset.id]); });
  }
  commit(layouts) {
    this.cards.forEach(c => { if (layouts[c.id]) c.layout = layouts[c.id]; });
    this.preview(layouts);
    this.changed?.();
  }
  // 숫자 칸·설정 패널에서 한 카드의 자리를 바꾼다
  setLayout(card, patch, how = "move") {
    const next = clampLayout({ ...card.layout, ...patch });
    this.commit(how === "resize" ? resizeCard(this.items(), card.id, next.w, next.h) : moveCard(this.items(), card.id, { x: next.x, y: next.y }));
  }
  cardBox(card, colors) {
    const box = Board.prototype.cardBox.call(this, card, colors);  // DashBoard의 인라인 설정 줄은 쓰지 않는다(설정은 오른쪽 패널)
    card.layout = clampLayout(card.layout);
    box.dataset.id = card.id;
    this.place(box, card.layout);
    box.classList.toggle("editing", this.editing === card);
    const head = box.querySelector(".bhead"), name = head.querySelector(".btitle");
    if (this.star.card === card.id) head.insertBefore(el("span", { className: "bbadge", textContent: "★", title: "북극성 지표" }), name);
    if (this.star.counters.includes(card.id)) head.insertBefore(el("span", { className: "bbadge", textContent: "⚑", title: "카운터 지표" }), name);
    head.insertBefore(el("button", { className: "ib", textContent: "⚙", title: "이 카드 설정", onclick: e => { e.stopPropagation(); this.edit(card); } }), head.lastChild);
    head.classList.add("movable");
    head.onpointerdown = e => this.startMove(e, card, box, head);
    const handle = el("i", { className: "bresize", title: "끌어서 크기를 바꿉니다(가로·세로 2~12칸)" });
    handle.onpointerdown = e => this.startResize(e, card, box, handle);
    box.append(handle);
    return box;
  }
  // 제목 줄을 끌어 옮긴다: 포인터가 가리키는 칸으로 미리보고, 놓으면 세로 압축한 자리를 저장한다.
  startMove(e, card, box, head) {
    if (e.button !== 0 || e.target.closest("input, button, select")) return;
    e.preventDefault();
    const m = this.metrics(), at = box.getBoundingClientRect(), grabX = e.clientX - at.left, grabY = e.clientY - at.top;
    head.setPointerCapture(e.pointerId);
    box.classList.add("dragging");
    let layouts = null;
    head.onpointermove = ev => {
      layouts = moveCard(this.items(), card.id, cellAt(ev.clientX, ev.clientY, m, grabX, grabY));
      this.preview(layouts);
    };
    head.onpointerup = head.onpointercancel = ev => {
      head.onpointermove = head.onpointerup = head.onpointercancel = null;
      head.releasePointerCapture?.(ev.pointerId);
      box.classList.remove("dragging");
      if (layouts) {
        box.addEventListener("click", c => c.stopPropagation(), { capture: true, once: true });  // 끈 뒤의 click이 카드 고르기로 번지지 않게
        this.commit(layouts);
      }
    };
  }
  // 오른쪽 아래 손잡이를 끌어 크기를 바꾼다
  startResize(e, card, box, handle) {
    if (e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    const m = this.metrics(), at = box.getBoundingClientRect();
    handle.setPointerCapture(e.pointerId);
    box.classList.add("dragging");
    let layouts = null;
    handle.onpointermove = ev => {
      const s = sizeAt(ev.clientX - at.left, ev.clientY - at.top, m);
      layouts = resizeCard(this.items(), card.id, s.w, s.h);
      this.preview(layouts);
    };
    handle.onpointerup = handle.onpointercancel = ev => {
      handle.onpointermove = handle.onpointerup = handle.onpointercancel = null;
      handle.releasePointerCapture?.(ev.pointerId);
      box.classList.remove("dragging");
      if (layouts) this.commit(layouts);
    };
  }
  // 고른 카드의 그림: 차트는 Chart.js, SVG·HTML 카드(KPI·표·생키 …)는 직렬화해서(dash_report.js). 순서는 카드 순서.
  get paints() { return true; }
  async pictures(ratio, mode, type) {
    const out = [], r = BOARD_RATIOS[ratio], root = document.documentElement, was = root.dataset.theme;
    root.dataset.theme = mode;  // 카드마다 바꿨다 되돌리면 화면이 카드 수만큼 깜빡인다: 한 번만 바꾼다(paintedCanvas는 같은 모드라 그대로 둔다)
    try {
      for (const card of this.cards.filter(c => this.picked.has(c) && !c.note)) {
        const body = this.grid.querySelector(`.bcard[data-id="${card.id}"] .bbody`);
        const width = r ? Math.round(PICTURE_HEIGHT * r) : body?.clientWidth || 640, height = r ? PICTURE_HEIGHT : (body?.clientHeight || 360) + TITLE_BAND;
        if (card.config) out.push({ name: card.title, data: chartPicture(card.config, mode, type, width, height, 2, card.title) });
        else out.push({ name: card.title, data: titledPicture(await paintedCanvas(card, mode, width, height - (card.title ? TITLE_BAND : 0), 2), card.title, mode, type) });
      }
    } finally {
      if (was === undefined) delete root.dataset.theme; else root.dataset.theme = was;
    }
    return out;
  }
  // 설정을 여는 카드(없으면 닫음)를 표시한다
  edit(card) {
    this.editing = card;
    this.grid.querySelectorAll(".bcard[data-id]").forEach(b => b.classList.toggle("editing", !!card && b.dataset.id === card.id));
    this.onEdit?.(card);
  }
}
