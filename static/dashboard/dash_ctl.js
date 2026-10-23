// 대시보드 기준일·기간·최신화·실시간 칸: 첫 화면(home.html)의 대시보드와 쿼리 화면의 대시보드 탭이 함께 쓴다(common.js, board.js, dash.js가 먼저 불려 있어야 한다).
// 글자를 읽는 규칙은 모두 서버(period.py)에 있고 여기서는 /api/period/day·parse·every로 읽은 결과만 보인다. 틀린 입력은 칸 아래에 이유를 보이고 이전 값을 둔다.
// 기준일(as_of)은 비면 오늘이고, 오늘이 아니면 "과거 시점을 보는 중"이며 실시간 갱신은 멈춘다(최신화 주기는 늘 실제 지금 시각 기준이다).
class DashCtl {
  // board: DashBoard. changed(fields): 칸을 바꿔 확정할 때 {as_of, period, refresh: {every}, live: {every}}를 받는다(저장은 쓰는 화면이 한다).
  // liveRun(): 실시간 한 번(카드 값을 다시 구해 그리기), visible(): 지금 이 대시보드가 보이는지.
  constructor(root, { board, changed, liveRun, visible = () => true }) {
    Object.assign(this, { board, changed, liveRun, visible, today: "", state: { as_of: "", period: "", refresh: "", live: "" }, liveOn: false, gen: 0, timer: null, seconds: 0, times: [] });
    const field = (label, input, note) => el("div", { className: "dc-field" }, el("label", { className: "hint", textContent: label }), input, note);
    this.asof = el("input", { placeholder: "오늘", title: "기준일: 2026-10-01, 2026.10.01, 20261001, 어제, 지난달 말. 비우면 오늘" });
    this.pick = el("input", { type: "date", className: "dc-pick", tabIndex: -1 });
    this.pickBtn = el("button", { className: "btn", textContent: "달력", title: "달력에서 기준일 고르기", onclick: () => this.pick.showPicker ? this.pick.showPicker() : this.pick.click() });
    this.asofNote = el("div", { className: "hint dc-note" });
    this.per = el("input", { placeholder: "비우면 카드의 원래 기간", title: "비우면 대시보드 기간을 걸지 않고 카드마다 자기 조건(기간 줄)대로 보입니다. 최근 N일·주·달·년, 2026-10, 2026-Q3, 2026, 시작 ~ 끝, 지난주·지난달·작년·이번 달…" });
    this.perNote = el("div", { className: "hint dc-note" });
    this.every = el("input", { placeholder: "비우면 안 함 (예: 1일)", title: `연결한 뒤 이 주기가 지난 카드만 다시 구합니다. 최소 ${minimumText(SERVER.dash.min_refresh)}` });
    this.lastNote = el("div", { className: "hint dc-note" });
    this.live = el("input", { placeholder: "예: 30초", title: "화면을 보는 동안 이 간격으로 카드를 다시 구합니다(앞 조회가 끝난 뒤 센다). 저장하지 않습니다" });
    this.liveBtn = el("button", { className: "btn", textContent: "시작", onclick: () => this.toggleLive() });
    this.liveNote = el("div", { className: "hint dc-note" });
    this.past = el("span", { className: "dc-past hidden", textContent: "과거 시점을 보는 중" });
    this.min = el("input", { type: "number", min: 0, placeholder: "제한 없음", title: "이 연결의 실시간 간격 최소(초). 비우거나 0이면 제한 없음" });
    this.minBtn = el("button", { className: "btn", textContent: "적용", onclick: () => this.saveMin() });
    this.minNote = el("div", { className: "hint dc-note" });
    root.className = "dctl";
    root.replaceChildren(field("기준일", el("div", { className: "row" }, this.asof, this.pickBtn, this.pick), this.asofNote), field("기간", this.per, this.perNote),
      field("최신화 주기", this.every, this.lastNote), field("실시간 간격", el("div", { className: "row" }, this.live, this.liveBtn), this.liveNote),
      field("이 연결의 실시간 최소(초)", el("div", { className: "row" }, this.min, this.minBtn), this.minNote), this.past);
    this.asof.onchange = () => this.apply(true);
    this.per.onchange = () => this.apply(true);
    this.pick.onchange = () => { if (this.pick.value) { this.asof.value = this.pick.value; this.apply(true); } };
    this.every.onchange = () => this.check(this.every, "refresh", this.lastNote);
    this.live.onchange = () => this.check(this.live, "live", this.liveNote);
    document.addEventListener("dash-connected", () => this.loadMin());  // 연결에 성공하면(dash_refresh.js) 연결 열쇠를 다시 받는다
    this.ready = api("/api/period/day", { text: "" }).then(d => { this.today = d.date; this.pick.max = d.date; });
  }
  note(box, text, bad) {
    box.textContent = text;
    box.className = "hint dc-note" + (bad ? " bad" : "");
  }
  // 저장된 대시보드({as_of, period, refresh, live})로 칸을 채우고 기간을 적용한다.
  async set(d) {
    await this.ready;
    this.stopLive("");
    this.state = { as_of: d.as_of || "", period: d.period || "", refresh: d.refresh?.every || "", live: d.live?.every || "" };
    this.asof.value = this.state.as_of;
    this.per.value = this.state.period;
    this.every.value = this.state.refresh;
    this.live.value = this.state.live;
    await this.apply(false);
    this.loadMin();
  }
  fields() {
    return { as_of: this.state.as_of, period: this.state.period, refresh: { every: this.state.refresh }, live: { every: this.state.live } };
  }
  // 기준일과 기간 칸을 서버로 읽어 보는 기간(기준일로 자른 것)과 직전 기간을 정한다. 틀리면 이유를 보이고 이전 값으로 되돌린다.
  async apply(commit) {
    await this.ready;
    const seq = this.applySeq = (this.applySeq ?? 0) + 1;  // 칸을 연달아 바꾸면 늦게 온 옛 응답이 새 기간을 덮지 않게 번호로 가린다
    const day = await api("/api/period/day", { text: this.asof.value });
    if (seq !== this.applySeq) return;
    if (day.error) { this.asof.value = this.state.as_of; return this.note(this.asofNote, day.error, true); }
    const past = day.date !== this.today, blank = !this.per.value.trim();
    const parsed = blank ? { span: { start: null, end: null, label: "기간을 걸지 않아요. 카드의 원래 조건대로 보입니다" }, previous: null } : await api("/api/period/parse", { text: this.per.value, as_of: day.date }), span = parsed.span;
    if (seq !== this.applySeq) return;
    if (span.error) { this.per.value = this.state.period; return this.note(this.perNote, span.error, true); }
    this.state.as_of = past ? day.date : "";
    this.state.period = this.per.value.trim();
    this.asof.value = this.state.as_of;
    this.note(this.asofNote, day.label);
    this.note(this.perNote, span.label + (span.days ? ` (${span.days}일)` : ""));
    this.past.classList.toggle("hidden", !past);
    if (past) this.stopLive("과거 시점을 보는 중이라 실시간 갱신은 멈춰 있어요");
    const ctx = parsed.context;  // 보는 기간(기준일로 자른 것)과 직전 기간은 서버(dash_refresh.context)가 구한 대로 쓴다
    await this.board.setRange(blank ? null : ctx?.range ?? null, blank ? null : ctx?.prev ?? null);
    if (commit) this.changed(this.fields());
  }
  // 최신화 주기·실시간 간격 칸을 서버로 검사한다(최신화는 1시간 이상, 실시간은 이 연결의 최소 이상).
  async check(input, kind, note) {
    const text = input.value.trim(), now = this.state[kind];
    if (text) {
      const d = await api("/api/period/every", { text, kind });
      if (d.error) { input.value = now; return this.note(note, d.error, true); }
      if (kind === "live") this.seconds = d.seconds;
    }
    this.state[kind] = text;
    this.note(note, text ? "" : kind === "refresh" ? "안 함" : "");
    this.changed(this.fields());
  }
  showLast(cards) {
    const times = cards.map(c => c.data?.computed_at).filter(Boolean).sort();
    this.note(this.lastNote, times.length ? `마지막 시각 ${times[times.length - 1].replace("T", " ")}` : "아직 구한 값이 없어요");
  }
  async loadMin() {
    try {
      const d = await api("/api/dash/live_min"), key = d.source?.key ?? "";
      if (this.board.connKey !== key) { this.board.connKey = key; this.board.draw(); }  // 지금 연결 열쇠: 다른 연결의 카드는 이전 값, 최근 행은 연결돼야 보임
      this.min.disabled = this.minBtn.disabled = !d.source;
      this.min.value = d.seconds || "";
      this.note(this.minNote, d.source ? d.source.label : "연결하면 정할 수 있어요");
    } catch (e) { this.note(this.minNote, e.message, true); }
  }
  async saveMin() {
    try {
      const d = await api("/api/dash/live_min", { seconds: +this.min.value || 0 });
      this.min.value = d.seconds || "";
      this.note(this.minNote, d.seconds ? `${d.source.label}: ${d.seconds}초 이상` : "제한 없음");
    } catch (e) { this.note(this.minNote, e.message, true); }
  }
  // 실시간 갱신 사슬: 앞 조회가 끝난 뒤 max(간격, 걸린 시간) 뒤에 다음을 시작하므로 겹치지 않는다. 간격보다 오래 걸리면 알린다.
  async toggleLive() {
    if (this.liveOn) return this.stopLive("멈췄어요");
    if (!this.state.live) return this.note(this.liveNote, "실시간 간격을 먼저 적어 주세요. 예: 30초", true);
    if (this.state.as_of) return this.note(this.liveNote, "과거 시점을 보는 중이라 실시간 갱신은 멈춰 있어요", true);
    this.seconds = (await api("/api/period/every", { text: this.state.live, kind: "live" })).seconds;
    this.liveOn = true;
    this.times = [];
    this.liveBtn.textContent = "멈추기";
    this.tick(++this.gen);
  }
  // gen: 시작할 때마다 새 번호. 멈췄다 바로 다시 시작해도 앞 사슬(아직 조회 중이던 tick)은 번호가 달라 끊긴다.
  async tick(gen) {
    if (!this.liveOn || gen !== this.gen) return;
    const interval = this.seconds * 1000;
    let took = 0;
    if (this.visible()) {
      const start = performance.now();
      try { const ran = await this.liveRun(); this.note(this.liveNote, ran === false ? "입력 중이라 미뤘어요" : `갱신함 ${new Date().toLocaleTimeString()}`); } catch (e) { this.note(this.liveNote, e.message, true); }
      took = performance.now() - start;
      this.times.push(took);
      if (took > interval) this.note(this.liveNote, `조회가 간격보다 오래 걸려요(평균 ${(this.times.reduce((a, b) => a + b, 0) / this.times.length / 1000).toFixed(1)}초)`, true);
    }
    if (this.liveOn && gen === this.gen) this.timer = setTimeout(() => this.tick(gen), Math.max(interval, took));
  }
  stopLive(text) {
    this.liveOn = false;
    this.gen++;
    clearTimeout(this.timer);
    this.liveBtn.textContent = "시작";
    if (text) this.note(this.liveNote, text);
  }
}
// 서버에 다시 구해 달라고 보낼 카드: 값은 빼고, 상태·경고·배치도가 "N분째"를 이어 가도록 나빠진 시각(data.since)만 남긴다.
const cardRequest = c => ({ ...DashBoard.plain(c), data: c.data?.since ? { since: c.data.since } : {} });
// 실시간·기간으로 받은 값은 화면에만 보이고 저장하지 않는다(docs/dashboard.md). showLive가 처음 덮을 때의 값을 c.stored에 두고, 저장할 때(storedCard)는 그 값을 쓴다.
// 다시 구하기처럼 c.data를 새로 넣으면 c.data !== c.liveData가 되어 그 값이 저장 대상이 된다.
function showLive(c, data) {
  if (!(c.liveData && c.data === c.liveData)) c.stored = c.data ?? {};
  c.data = c.liveData = data;
}
const storedCard = c => ({ ...DashBoard.plain(c), data: c.liveData && c.data === c.liveData ? c.stored : c.data });
// 실시간 한 번: 연결이 있는 카드의 값을 서버(/api/dash/cards/compute)에서 다시 구해 카드에 넣고 그린다(저장하지 않는다). 다른 연결의 카드는 서버가 건너뛴다.
async function liveCards(board, connected) {
  if (!connected) throw new Error("먼저 데이터에 연결하세요.");
  const cards = board.cards.filter(c => c.source?.key && c.id).map(cardRequest);
  (await api("/api/dash/cards/compute", { cards, ctx: board.ctx() })).results.forEach(r => { const c = board.cards.find(x => x.id === r.id); if (c && r.data) showLive(c, r.data); });
  await board.recompare();
}
// 기간 기준 날짜 컬럼이 있는 날짜 X축이 아닌 카드(cards)를 보는 기간(board.ctx())으로 다시 구한다. 연결이 없거나 다른 연결의 카드는 이유를 카드에 적고 이전 값을 둔다.
let rangeCall = 0;  // 기간으로 다시 구하는 요청 번호: 기간을 연달아 바꾸면 늦게 온 옛 응답은 버린다(카드마다 가장 최근 요청의 것만 받는다)
async function recomputeRange(board, cards, connected) {
  const ctx = board.ctx(), call = ++rangeCall;
  const todo = cards.filter(c => {
    c.rangeCall = call;
    c.rangeNote = !connected ? "연결하면 이 기간으로 구합니다" : c.source.key && board.connKey && !sameSource(c.source.key, board.connKey) ? "다른 연결의 카드라 이 기간으로 구하지 못했어요" : "";
    return !c.rangeNote && c.id;
  });
  if (todo.length) {
    try {
      (await api("/api/dash/cards/compute", { cards: todo.map(cardRequest), ctx })).results.forEach(r => {
        const c = todo.find(x => x.id === r.id);
        if (c && c.rangeCall === call) { if (r.data) { showLive(c, r.data); c.rangeNote = ""; } else c.rangeNote = r.error; }
      });
    } catch (e) { todo.forEach(c => { if (c.rangeCall === call) c.rangeNote = e.message; }); }
  }
  await board.recompare();  // 고유 개수 카드의 비교는 방금 받은 값으로 다시 만든다(끝에 그린다)
}
// 초를 "1시간"·"30분"·"45초" 글자로(서버 dash_refresh.minimum_text와 같다)
const minimumText = seconds => seconds % 3600 === 0 ? `${seconds / 3600}시간` : seconds % 60 === 0 ? `${seconds / 60}분` : `${seconds}초`;
