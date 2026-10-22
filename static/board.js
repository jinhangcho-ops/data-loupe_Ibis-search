// 그래프 카드 모음: 쿼리 화면의 대시보드가 쓴다. dash.js의 DashBoard가 이어받아 카드마다 차트 종류·색을 고르는 줄을 더한다(Chart.js와 common.js가 먼저 불려 있어야 한다).
// 카드를 누르면 고르고(여럿), [다운로드]는 고른 그래프를 각각 그림 파일로 저장한다.

// 그 모드의 색(CSS 변수). 화면 모드와 다른 모드의 색도 읽을 수 있게 잠깐 바꿨다가 되돌린다.
function themeColors(theme) {
  const root = document.documentElement, now = root.dataset.theme;
  root.dataset.theme = theme;
  const css = getComputedStyle(root), color = name => css.getPropertyValue(name).trim();
  const colors = { card: color("--card"), head: color("--bg"), ink: color("--text"), text: color("--muted"), line: color("--line"),
                   series: [1, 2, 3, 4, 5, 6, 7, 8].map(i => color("--c" + i)) };  // 계열 색. 넣은 순서대로 쓰고, 넘치면 처음부터 다시 쓴다.
  root.dataset.theme = now;
  return colors;
}

// 그래프 하나를 그림(data URL)으로. config(colors, options)는 그 색으로 그릴 Chart.js 설정이다.
// width·height는 그릴 크기(화면 픽셀), scale은 배율(그림 크기 = 크기 × 배율). title을 주면 위에 이름 줄을 더한다(그 줄까지 합쳐 height). 배경은 그 모드의 카드 색.
const TITLE_BAND = 36;
function chartPicture(config, mode, type, width, height, scale, title = "") {
  const colors = themeColors(mode), band = title ? TITLE_BAND : 0;
  const copy = Object.assign(document.createElement("canvas"), { width, height: height - band });
  const redrawn = new Chart(copy, config(colors, { responsive: false, animation: false, devicePixelRatio: scale }));
  const out = Object.assign(document.createElement("canvas"), { width: copy.width, height: copy.height + band * scale });
  const ctx = out.getContext("2d");
  ctx.fillStyle = colors.card;
  ctx.fillRect(0, 0, out.width, out.height);
  if (title) {
    ctx.fillStyle = colors.ink;
    ctx.font = `600 ${15 * scale}px "Segoe UI", "Malgun Gothic", sans-serif`;
    ctx.textBaseline = "middle";
    ctx.fillText(title, 14 * scale, band * scale / 2, out.width - 28 * scale);
  }
  ctx.drawImage(copy, 0, band * scale);
  redrawn.destroy();
  return out.toDataURL("image/" + type);
}

// 그림 비율(가로/세로). screen은 카드에 보이는 모양 그대로. 고른 비율은 세로 PICTURE_HEIGHT에 2배로 그린다(예: 16:9 → 1600×900).
const BOARD_RATIOS = { square: 1, "4:3": 4 / 3, wide: 16 / 9 };
const PICTURE_HEIGHT = 450;

// 카드 모음. cards: [{ title, ratio(가로/세로), config(colors, options), note }]. note가 있으면 그래프 대신 그 글(다시 구하는 중, 오류)을 보인다.
// changed(): 카드나 고른 것이 바뀔 때마다(개수 표시, 저장). empty: 카드가 없을 때의 안내.
class Board {
  constructor(grid, { empty, changed }) {
    Object.assign(this, { grid, empty, changed, cards: [], picked: new Set(), charts: [] });
  }
  set(cards) {
    this.cards = cards;
    this.picked = new Set([...this.picked].filter(c => cards.includes(c)));
    this.draw();
  }
  // 지금 화면 모드의 색으로 다시 그린다. 감춰져 있으면 크기를 몰라 그리지 않는다(보일 때 다시 부른다).
  draw() {
    this.charts.forEach(c => c.destroy());
    this.charts = [];
    if (!this.cards.length) this.grid.replaceChildren(el("div", { className: "hint board-empty", textContent: this.empty }));
    else if (this.grid.offsetParent) {
      const colors = themeColors(document.documentElement.dataset.theme);
      this.grid.replaceChildren(...this.cards.map(card => this.cardBox(card, colors)));
    }
    this.changed?.();
  }
  cardBox(card, colors) {
    const canvas = el("canvas");
    const body = el("div", { className: "bbody" }, card.note ? el("div", { className: "hint bnote", textContent: card.note }) : canvas);
    body.style.setProperty("--ratio", card.ratio || 16 / 9);
    const title = el("input", { className: "btitle", value: card.title, title: "그래프 이름. 그림 파일 이름이 됩니다" });
    title.onclick = e => e.stopPropagation();
    title.onchange = () => { card.title = title.value.trim() || card.title; title.value = card.title; this.changed?.(); };
    const remove = el("button", { className: "ib", textContent: "×", title: "이 그래프를 뺍니다" });
    remove.onclick = e => { e.stopPropagation(); this.set(this.cards.filter(c => c !== card)); };
    const box = el("div", { className: "bcard" + (this.picked.has(card) ? " sel" : ""), title: "누르면 고릅니다(여럿 고를 수 있습니다)" },
      el("div", { className: "bhead" }, el("i", { className: "bcheck" }), title, remove), body);
    box.onclick = () => this.toggle(card, box);
    card.canvas = canvas;
    if (!card.note) this.charts.push(new Chart(canvas, card.config(colors)));
    return box;
  }
  toggle(card, box) {
    if (this.picked.has(card)) this.picked.delete(card); else this.picked.add(card);
    box.classList.toggle("sel", this.picked.has(card));
    this.changed?.();
  }
  pickAll(on) {
    this.picked = new Set(on ? this.cards : []);
    this.grid.querySelectorAll(".bcard").forEach(b => b.classList.toggle("sel", on));
    this.changed?.();
  }
  // 고른 그래프(글만 보이는 카드는 빼고)의 그림 [{ name, data }]. 순서는 카드 순서.
  pictures(ratio, mode, type) {
    return this.cards.filter(c => this.picked.has(c) && !c.note).map(c => {
      const r = BOARD_RATIOS[ratio], width = r ? Math.round(PICTURE_HEIGHT * r) : c.canvas.clientWidth;
      const height = r ? PICTURE_HEIGHT : c.canvas.clientHeight + TITLE_BAND;
      return { name: c.title, data: chartPicture(c.config, mode, type, width, height, 2, c.title) };
    });
  }
}

// 고른 그래프를 그림 파일로 저장한다. 프로그램 창에서는 폴더를 한 번 묻고 그 폴더에 모두 저장한다(같은 이름은 번호를 붙인다).
// 창 없이 열었으면(브라우저) 하나씩 내려받는다. err: 결과를 쓸 칸의 id.
async function saveBoard(board, { ratio, mode, type, err }) {
  const pictures = board.pictures(ratio, mode, type), skipped = [...board.picked].filter(c => c.note).length;
  if (!pictures.length) return say(err, skipped ? "고른 그래프가 아직 그려지지 않았습니다." : "다운로드할 그래프를 고르세요. 카드를 누르면 고릅니다.");
  const d = await api("/api/images", { images: pictures });
  const more = skipped ? ` 그려지지 않은 ${skipped}개는 뺐습니다.` : "";
  if (d.cancelled) return say(err, "");
  if (d.window === false) {
    pictures.forEach(p => download(p.data, `${p.name}.${type === "jpeg" ? "jpg" : "png"}`));
    return say(err, `${pictures.length}개를 내려받았습니다.${more}`, true);
  }
  say(err, `${d.saved.length}개를 저장했습니다: ${d.folder}${more}`, true);
}
