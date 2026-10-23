// 대시보드 편집 화면의 두 패널: 왼쪽 카드 모음(끌어 놓거나 더블클릭으로 빈 틀 카드를 더함)과 오른쪽 카드 설정(이름·자리·데이터·그 카드의 설정 줄·북극성/카운터·차트 선반).
// board는 dash_edit.js의 EditBoard. dash_menu.js(initCardMenu)가 데이터를 정하는 칸을, dash_shelf.js가 차트 선반을 맡는다.

// 카드 모음에 보이는 틀 목록: [{group, kind, type?, name, help}]. 차트는 종류(막대·라인·산점도·버블 …)마다 한 칸.
function galleryItems() {
  const charts = Object.entries(SERVER.dash.types).map(([type, name]) => ({ group: "차트", kind: "chart", type, name, help: type === "scatter" || type === "bubble" ? "X와 값 하나가 점의 위치" : "X축 컬럼과 값(최대 5개)" }));
  const some = (group, list) => list.map(([kind, name, help]) => ({ group, kind, name, help }));
  return [...charts,
    ...some("값·목표", [["kpi", "KPI", "큰 값·비교·스파크라인"], ["donut_kpi", "도넛 KPI", "가운데+꼭지점 칸"], ["gauge", "게이지", "항목 몫"], ["summary", "요약 문장", "목표 대비 문장"],
                      ["stripes", "칸 채우기", "목표까지 칸"], ["blocks", "블록", "양을 블록으로"], ["target_trend", "목표 추이", "목표선과 일 값"], ["dual_trend", "두 계열 추이", "두 값을 겹쳐"]]),
    ...some("표·글", [["table", "표", "집계 또는 원래 행"], ["sentence", "문장", "빈칸에 값·비교"], ["recent_rows", "최근 행", "연결할 때 보임"]]),
    ...some("모니터링", [["status", "상태", "정상·주의·이상"], ["alerts", "경고 목록", "N분째·영향"], ["map", "배치도", "끌어 옮기는 점"], ["deviation", "편차 표", "엔티티×시간대"]]),
    ...some("흐름", [["funnel", "흐름(퍼널)", "단계별 도달"]])];
}
const CARD_MIME = "application/x-dash-card";
const KIND_NAMES = { chart: "차트", analysis: "분석", metric: "지표", kpi: "KPI", donut_kpi: "도넛 KPI", gauge: "게이지", summary: "요약 문장", recent_rows: "최근 행", dual_trend: "두 계열 추이", stripes: "칸 채우기",
                     blocks: "블록", target_trend: "목표 추이", map: "배치도", alerts: "경고 목록", deviation: "편차 표", funnel: "흐름(퍼널)", table: "표", status: "상태", sentence: "문장" };

// 빈 틀 카드(source 없음): 카드 모음·템플릿이 만든다. 데이터는 설정 패널의 [데이터]에서 정한다.
function shellOf(spec, layout, newId) {
  return { id: newId(), kind: spec.kind, title: spec.kind === "chart" ? spec.name : spec.name, layout, colors: { series: {}, parts: {}, order: [], parts_order: [] }, view: { type: spec.type ?? "" }, source: {}, data: {} };
}

// 카드 모음: 칸을 끌어 격자에 놓거나(놓은 칸 자리에) 더블클릭하면(맨 아래) 빈 틀 카드가 생긴다. host.add(spec, cell|null)
function initGallery(root, host) {
  const tiles = galleryItems(), groups = [...new Set(tiles.map(t => t.group))];
  root.replaceChildren(el("div", { className: "hint", textContent: "끌어서 격자에 놓거나 더블클릭하면 빈 카드가 생겨요. 데이터는 카드의 ⚙에서 정합니다." }),
    ...groups.flatMap(g => [el("h5", { textContent: g }), el("div", { className: "gal" }, ...tiles.filter(t => t.group === g).map(t => {
      const tile = el("div", { className: "gal-tile", draggable: true, title: t.help }, el("b", { textContent: t.name }), el("span", { className: "hint", textContent: t.help }));
      tile.ondragstart = e => { e.dataTransfer.setData(CARD_MIME, JSON.stringify(t)); e.dataTransfer.setData("text/plain", t.name); e.dataTransfer.effectAllowed = "copy"; };
      tile.ondblclick = () => host.add(t, null);
      return tile;
    }))]),
    el("div", { className: "hint", textContent: "분석 화면의 결과와 쿼리 화면의 차트·지표는 각 화면의 [대시보드에 추가]로 넣어요." }));
}
// 격자에 카드 모음 칸을 놓는 자리: 놓으면 host.add(spec, {x, y})
function acceptGalleryDrops(board, host) {
  const grid = board.grid;
  grid.addEventListener("dragover", e => { if ([...e.dataTransfer.types].includes(CARD_MIME)) { e.preventDefault(); grid.classList.add("drop-ready"); } });
  grid.addEventListener("dragleave", e => { if (e.target === grid) grid.classList.remove("drop-ready"); });
  grid.addEventListener("drop", e => {
    grid.classList.remove("drop-ready");
    const raw = e.dataTransfer.getData(CARD_MIME);
    if (!raw) return;
    e.preventDefault();
    host.add(JSON.parse(raw), cellAt(e.clientX, e.clientY, board.metrics()));
  });
}

// 오른쪽 설정 패널. host = { board, menu(initCardMenu의 반환), recompute(card), changed(), star(): 북극성 정보, setStar(star), columns(card): 그 카드 테이블의 [[이름, 타입]], newId }
class CardPanel {
  constructor(root, host) {
    Object.assign(this, { root, host, card: null, builderOpen: false });
  }
  close() {
    this.card = null;
    this.root.classList.add("hidden");
    this.host.menuTarget = null;
    this.host.popRoot.append(this.host.menuRoot);
    this.host.board.edit(null);
  }
  async open(card) {
    if (!card) return this.close();
    await this.host.prepare?.(card);
    this.card = card;
    this.note = "";
    this.builderOpen = isShell(card);
    this.show();
  }
  field(label, ...inputs) { return el("label", { className: "hint pfield" }, el("span", { textContent: label }), ...inputs); }
  // 한 번 그린다(카드가 바뀌면 다시 부른다)
  show() {
    const { board } = this.host, card = this.card;
    if (!card) return;
    this.root.classList.remove("hidden");
    this.host.popRoot.append(this.host.menuRoot);  // 입력 칸은 기본으로 [카드 추가] 메뉴에 둔다(데이터를 열면 아래에서 이 패널로 옮긴다)
    const err = el("div", { className: "err" });
    err.textContent = this.note ?? "";  // 직전 동작(선반 반영 등)의 결과 글은 다시 그려도 남긴다(다른 카드를 열면 지운다)
    const name = el("input", { value: card.title, onchange: e => { card.title = e.target.value.trim() || card.title; e.target.value = card.title; this.host.changed(); board.draw(); } });
    const num = (key, min, max) => el("input", { type: "number", min, max, value: card.layout[key], style: "width:64px", onchange: e => {
      const v = +e.target.value, bad = !Number.isInteger(v) || v < min || v > max;
      if (bad) { e.target.value = card.layout[key]; err.textContent = `${{ x: "가로 위치", y: "세로 위치", w: "가로 크기", h: "세로 크기" }[key]}는 ${min}~${max}의 정수예요. 이전 값을 그대로 뒀어요.`; return; }
      err.textContent = "";
      board.setLayout(card, { [key]: v }, key === "w" || key === "h" ? "resize" : "move");
      this.show();
    } });
    const place = el("div", { className: "prow" }, this.field("가로 칸", num("w", 2, 12)), this.field("세로 칸", num("h", 2, 12)), this.field("x", num("x", 0, 11)), this.field("y", num("y", 0, 999)));
    const head = el("div", { className: "phead" }, el("b", { textContent: KIND_NAMES[card.kind] ?? card.kind }), el("button", { className: "ib", textContent: "×", title: "닫기", onclick: () => this.close() }));
    const again = el("button", { className: "btn", textContent: "값 다시 구하기", title: "이 카드만 지금 연결에서 다시 구합니다", onclick: () => busy(again, async () => { try { await this.host.recompute(card); err.textContent = ""; this.show(); } catch (e) { err.textContent = e.message; } }) });
    const fills = [this.dataSection(card, err)];
    const settings = isShell(card) ? "" : board.tools(card);  // 종류·단위·색·목표·값 모양 같은 그 카드의 설정 줄(dash.js)
    this.root.replaceChildren(head, this.field("이름", name), place, ...fills, ...(settings ? [el("h5", { textContent: "설정" }), settings] : []), this.starSection(card), ...this.shelfSection(card, err), again, err);
  }
  // 데이터: 비어 있거나 [다시 정하기]를 누르면 dash_menu의 입력 칸(테이블·컬럼·집계)을 보인다. 정하면 이 카드에 채운다(자리·id는 그대로).
  dataSection(card, err) {
    const src = card.source ?? {}, shell = isShell(card);
    const what = shell ? "아직 데이터가 없어요" : src.recipe ? `테이블 ${src.table ?? ""} · 분석 결과` : src.metric_id ? `지표 ${src.metric_id}` : `테이블 ${src.table ?? ""}${src.date ? ` · 기간 기준 ${src.date}` : ""}`;
    const holder = el("div", { className: "pdata" }), toggle = el("button", { className: "btn", textContent: this.builderOpen ? "접기" : shell ? "데이터 정하기" : "데이터 다시 정하기",
      onclick: () => { this.builderOpen = !this.builderOpen; this.show(); } });
    if (this.builderOpen) {
      holder.append(this.host.menuRoot);
      this.host.menu.preset(card.kind === "analysis" || card.kind === "metric" ? "kpi" : card.kind, card.view?.type ?? "");
      this.host.menu.refill();
      this.host.menuTarget = card;
    } else this.host.menuTarget = null;
    return el("div", {}, el("h5", { textContent: "데이터" }), el("div", { className: "hint", textContent: what }), toggle, holder);
  }
  // 북극성·카운터: 북극성은 대시보드에서 가장 중요한 지표 하나(★), 카운터는 북극성을 올리다 나빠지면 안 되는 지표(⚑, 한계를 벗어나면 위쪽 경고에 올라온다)
  starSection(card) {
    const star = { card: "", counters: [], limits: {}, ...this.host.star() }, isStar = star.card === card.id, isCounter = star.counters.includes(card.id), lim = star.limits?.[card.id] ?? { op: ">=", value: "" };
    const set = next => { this.host.setStar(next); this.show(); };
    const north = el("button", { className: "btn" + (isStar ? " p" : ""), textContent: isStar ? "★ 북극성 지표 (해제)" : "★ 북극성 지표로", title: "대시보드에서 가장 중요한 지표 하나",
      onclick: () => set({ ...star, card: isStar ? "" : card.id, counters: star.counters.filter(c => c !== card.id) }) });
    const counter = el("button", { className: "btn" + (isCounter ? " p" : ""), textContent: isCounter ? "⚑ 카운터 지표 (해제)" : "⚑ 카운터 지표로", title: "북극성을 올리다 나빠지면 안 되는 지표(예: 환불률)",
      onclick: () => { const limits = { ...star.limits }; if (isCounter) delete limits[card.id]; set({ ...star, card: star.card === card.id ? "" : star.card, counters: isCounter ? star.counters.filter(c => c !== card.id) : [...star.counters, card.id], limits }); } });
    const parts = [el("h5", { textContent: "북극성·카운터" }), el("div", { className: "prow" }, north, counter)];
    if (isCounter) {
      const op = el("select", {}, el("option", { value: ">=", textContent: "이 값 이상이어야 함", selected: lim.op === ">=" }), el("option", { value: "<=", textContent: "이 값 이하여야 함", selected: lim.op === "<=" }));
      const value = el("input", { type: "number", step: "any", value: lim.value, placeholder: "한계 값", style: "width:100px" });
      const note = el("div", { className: "hint", textContent: "카운터의 지금 값이 한계를 벗어나면 대시보드 위쪽 경고에 올라와요." });
      const save = el("button", { className: "btn", textContent: "한계 저장", onclick: () => {
        if (value.value === "" || !Number.isFinite(+value.value)) return void (note.textContent = "한계는 숫자로 적어 주세요.");
        set({ ...star, limits: { ...star.limits, [card.id]: { op: op.value, value: +value.value } } });
      } });
      parts.push(el("div", { className: "prow" }, op, value, save), note);
    }
    return el("div", {}, ...parts);
  }
  // 차트 선반(차트 카드): X축·값·색(나누기)·필터를 컬럼 칩을 끌어 놓아 바꾼다. 바꾸면 그 카드를 다시 구한다.
  shelfSection(card, err) {
    const body = card.source?.body;
    if (card.kind !== "chart" || !body || !this.host.columns) return [];
    const cols = this.host.columns(card);
    if (!cols.length) return [el("h5", { textContent: "차트 칸" }), el("div", { className: "hint", textContent: "연결하면 컬럼 칩을 끌어 X축·값·색·필터를 바꿀 수 있어요." })];
    const state = () => ({ x: body.x ?? "", series: (body.series ?? []).map(s => ({ y: s.y ?? null, agg: s.agg })), split: body.split ?? "", conditions: (body.conditions ?? []).map(c => c.line ?? c) });
    const say = text => { this.note = text; err.textContent = text; };
    const panel = shelfPanel({ columns: () => cols, state, say, change: async (next, why) => {
      const before = JSON.stringify(body);
      body.x = next.x; body.series = next.series.map(s => ({ y: s.y, agg: s.agg, ...(body.series.find(o => o.y === s.y && o.agg === s.agg)?.opts ? { opts: body.series.find(o => o.y === s.y && o.agg === s.agg).opts } : {}) }));
      body.split = next.split; body.conditions = next.conditions;
      if (!body.split) delete body.split;
      try { await this.host.recompute(card); this.note = `${why} 반영했어요.`; this.host.changed(); }
      catch (e) { Object.keys(body).forEach(k => delete body[k]); Object.assign(body, JSON.parse(before)); this.note = `${e.message} 이전 설정으로 돌려놨어요.`; }
      this.show();
    } });
    return [el("h5", { textContent: "차트 칸 (컬럼 칩을 끌어 놓기)" }), panel.el];
  }
}
