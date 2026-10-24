// 대시보드 그림·보고서 저장: 차트 카드는 Chart.js로 다시 그리고, SVG·HTML로 직접 그리는 카드(KPI·표·생키 …)는 직렬화(XMLSerializer)해 Image로 읽어 canvas에 그린다.
// 보고서는 모든 카드를 격자 자리대로 큰 canvas 한 장에 모은다(PNG, 또는 JPEG → /api/dash/pdf가 1쪽 PDF로). board.js, dash.js, dash_grid.js가 먼저 불려 있어야 한다.
const REPORT = { col: 100, gap: 10, pad: 24, head: 56, band: 34 };  // 보고서 한 칸의 너비(px), 칸 간격, 바깥 여백, 위쪽 제목 줄, 카드 안의 이름 줄

// 격자 자리 → 보고서 그림 안의 사각형. 돌려주는 값: {width, height, rects: {카드 id: {x, y, w, h}}} (그림 크기 = 이 크기 × 배율)
function reportLayout(cards, { col, gap, pad, head } = REPORT) {
  const rects = {};
  let rows = 0;
  for (const c of cards) {
    const l = clampLayout(c.layout);
    rects[c.id] = { x: pad + l.x * (col + gap), y: head + l.y * (GRID_ROW + gap), w: l.w * col + (l.w - 1) * gap, h: l.h * GRID_ROW + (l.h - 1) * gap };
    rows = Math.max(rows, l.y + l.h);
  }
  return { width: pad * 2 + GRID_COLS * col + (GRID_COLS - 1) * gap, height: head + Math.max(1, rows) * (GRID_ROW + gap) - gap + pad, rects };
}

// 요소와 자손의 계산된 스타일을 style 속성에 박는다(직렬화한 그림에는 페이지의 CSS가 없으므로).
// 모든 속성(350여 개)을 박으면 500행 표가 5초·57MB가 되므로 그리는 데 쓰이는 속성만 박는다.
const INLINE_PROPS = ("display position top right bottom left float box-sizing width height min-width max-width min-height max-height z-index overflow-x overflow-y text-overflow visibility opacity transform transform-origin box-shadow aspect-ratio object-fit " +
  "margin-top margin-right margin-bottom margin-left padding-top padding-right padding-bottom padding-left " +
  "border-top-width border-right-width border-bottom-width border-left-width border-top-style border-right-style border-bottom-style border-left-style border-top-color border-right-color border-bottom-color border-left-color " +
  "border-top-left-radius border-top-right-radius border-bottom-right-radius border-bottom-left-radius border-collapse border-spacing table-layout " +
  "background-color background-image background-size background-position background-repeat color font-family font-size font-weight font-style line-height letter-spacing text-align text-decoration-line text-transform text-indent vertical-align white-space word-break overflow-wrap " +
  "flex-direction flex-wrap flex-grow flex-shrink flex-basis align-items align-self align-content justify-content justify-items order row-gap column-gap grid-template-columns grid-template-rows grid-column-start grid-column-end grid-row-start grid-row-end grid-auto-flow " +
  "color-scheme font-variant-numeric fill fill-opacity stroke stroke-width stroke-opacity stroke-dasharray stroke-linecap stroke-linejoin text-anchor dominant-baseline").split(" ");
function inlineStyles(source, copy) {
  const css = getComputedStyle(source);
  copy.setAttribute("style", INLINE_PROPS.map(name => { const v = css.getPropertyValue(name); return v === "" || (name !== "display" && (v === "none" || v === "normal")) ? "" : `${name}:${v};`; }).join(""));  // 빈 값·기본값(none·normal)은 복사본에서도 기본이라 뺀다
  [...source.children].forEach((child, i) => inlineStyles(child, copy.children[i]));
}

// 요소 하나를 width × height 그림(Image)으로: 스타일을 박은 복사본을 SVG foreignObject에 넣어 직렬화한다
function nodeToImage(node, width, height) {
  const copy = node.cloneNode(true);
  inlineStyles(node, copy);
  Object.assign(copy.style, { position: "static", left: "0px", top: "0px", margin: "0px" });  // 화면 밖에 둔 무대의 자리는 그림에 넣지 않는다
  const wrap = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  wrap.setAttribute("xmlns", "http://www.w3.org/2000/svg");
  wrap.setAttribute("width", width);
  wrap.setAttribute("height", height);
  const fo = document.createElementNS("http://www.w3.org/2000/svg", "foreignObject");
  fo.setAttribute("width", "100%");
  fo.setAttribute("height", "100%");
  if (copy.namespaceURI !== "http://www.w3.org/2000/svg") copy.setAttribute("xmlns", "http://www.w3.org/1999/xhtml");
  fo.append(copy);
  wrap.append(fo);
  const url = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(new XMLSerializer().serializeToString(wrap));
  return new Promise((resolve, reject) => {
    const img = new Image(width, height);
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error("카드를 그림으로 바꾸지 못했어요."));
    setTimeout(() => reject(new Error("카드를 그림으로 바꾸는 데 너무 오래 걸렸어요.")), 15000);
    img.src = url;
  });
}

// 지도 카드의 타일(<image href="https://…">): 직렬화한 SVG 그림은 바깥 그림을 불러오지 못하므로, CORS를 허용하는 img로 받아 data URL로 바꿔 넣는다. 못 받은 타일은 뺀다.
function inlineTiles(root) {
  return Promise.all([...root.querySelectorAll("image")].filter(n => /^https?:/.test(n.getAttribute("href") ?? "")).map(n => new Promise(done => {
    const img = new Image(), giveUp = setTimeout(() => { n.remove(); done(); }, 8000);
    img.crossOrigin = "anonymous";
    img.onload = () => {
      clearTimeout(giveUp);
      const c = Object.assign(document.createElement("canvas"), { width: img.naturalWidth, height: img.naturalHeight });
      c.getContext("2d").drawImage(img, 0, 0);
      n.setAttribute("href", c.toDataURL("image/png"));
      done();
    };
    img.onerror = () => { clearTimeout(giveUp); n.remove(); done(); };
    img.src = n.getAttribute("href");
  })));
}

// SVG·HTML 카드(card.paint)를 그 모드의 색으로 width × height canvas에. 화면 밖에 잠깐 그려 스타일을 박는다.
async function paintedCanvas(card, mode, width, height, scale) {
  const root = document.documentElement, now = root.dataset.theme, colors = themeColors(mode);
  const stage = el("div", { style: `position:fixed; left:-99999px; top:0; width:${width}px; height:${height}px; overflow:hidden; background:${colors.card}; color:${colors.ink}`, className: "bbody paint" });
  document.body.append(stage);
  root.dataset.theme = mode;
  try {
    stage.append(card.paint(colors));
    await new Promise(r => setTimeout(r, 30));  // 그려질 틈(창이 가려져 있어도 도는 타이머)
    await inlineTiles(stage);
    const img = await nodeToImage(stage, width, height);
    const out = Object.assign(document.createElement("canvas"), { width: Math.round(width * scale), height: Math.round(height * scale) });
    const ctx = out.getContext("2d");
    ctx.fillStyle = colors.card;
    ctx.fillRect(0, 0, out.width, out.height);
    ctx.drawImage(img, 0, 0, out.width, out.height);
    return out;
  } finally {
    root.dataset.theme = now;
    stage.remove();
  }
}

// data URL → Image
const loadImage = url => new Promise((resolve, reject) => { const img = new Image(); img.onload = () => resolve(img); img.onerror = () => reject(new Error("그림을 읽지 못했어요.")); img.src = url; });

// 카드 하나의 그림(canvas): 차트는 Chart.js, 그 밖은 paintedCanvas
async function cardCanvas(card, mode, width, height, scale) {
  if (card.config) {
    const img = await loadImage(chartPicture(card.config, mode, "png", width, height, scale, ""));
    const out = Object.assign(document.createElement("canvas"), { width: img.naturalWidth, height: img.naturalHeight });
    out.getContext("2d").drawImage(img, 0, 0);
    return out;
  }
  return paintedCanvas(card, mode, width, height, scale);
}

// 보고서 canvas: 제목 줄 + 카드마다 격자 자리의 상자(이름 줄 + 그림). 아직 그려지지 않은 카드(note)는 그 글만.
async function reportCanvas(board, title, { mode = "light", scale = 2 } = {}) {
  const colors = themeColors(mode), cards = board.cards, lay = reportLayout(cards);
  const out = Object.assign(document.createElement("canvas"), { width: lay.width * scale, height: lay.height * scale });
  const ctx = out.getContext("2d");
  const font = (px, weight = 400) => `${weight} ${px * scale}px "Segoe UI", "Malgun Gothic", sans-serif`;
  ctx.fillStyle = colors.head;
  ctx.fillRect(0, 0, out.width, out.height);
  ctx.fillStyle = colors.ink;
  ctx.font = font(22, 700);
  ctx.textBaseline = "middle";
  ctx.fillText(title, REPORT.pad * scale, (REPORT.head / 2 + 4) * scale, (lay.width - REPORT.pad * 2) * scale);
  ctx.fillStyle = colors.text;
  ctx.font = font(12);
  ctx.textAlign = "right";
  ctx.fillText(new Date().toLocaleDateString("sv"), (lay.width - REPORT.pad) * scale, (REPORT.head / 2 + 4) * scale);
  ctx.textAlign = "left";
  for (const card of cards) {
    const r = lay.rects[card.id], [x, y, w, h] = [r.x * scale, r.y * scale, r.w * scale, r.h * scale];
    ctx.fillStyle = colors.card;
    ctx.strokeStyle = colors.line;
    ctx.lineWidth = scale;
    ctx.beginPath();
    ctx.roundRect(x, y, w, h, 12 * scale);
    ctx.fill();
    ctx.stroke();
    ctx.fillStyle = colors.ink;
    ctx.font = font(14, 600);
    ctx.fillText(card.title, x + 14 * scale, y + REPORT.band / 2 * scale, w - 28 * scale);
    const bodyH = r.h - REPORT.band - 8, bodyW = r.w - 16;
    if (card.note || bodyH < 20) {
      ctx.fillStyle = colors.text;
      ctx.font = font(12);
      ctx.fillText(card.note ?? "", x + 14 * scale, y + (REPORT.band + 20) * scale, w - 28 * scale);
      continue;
    }
    try { ctx.drawImage(await cardCanvas(card, mode, bodyW, bodyH, scale), x + 8 * scale, y + REPORT.band * scale, bodyW * scale, bodyH * scale); }
    catch (e) {
      ctx.fillStyle = colors.text;
      ctx.font = font(12);
      ctx.fillText(e.message, x + 14 * scale, y + (REPORT.band + 20) * scale, w - 28 * scale);
    }
  }
  return out;
}

// 보고서를 PNG로 저장(창에서는 /api/images의 폴더 대화상자, 브라우저에서는 내려받기)
async function saveReportPng(board, title, { mode, err }) {
  const data = (await reportCanvas(board, title, { mode })).toDataURL("image/png");
  const d = await api("/api/images", { images: [{ name: title, data }] });
  if (d.cancelled) return say(err, "");
  if (d.window === false) { download(data, `${title}.png`); return say(err, "보고서를 PNG로 내려받았습니다.", true); }
  say(err, `보고서를 저장했습니다: ${d.folder}/${d.saved[0]}`, true);
}
// 보고서를 PDF로 저장: JPEG 한 장을 서버(dash_pdf.py)가 1쪽 PDF로 만든다
async function saveReportPdf(board, title, { mode, err }) {
  const data = (await reportCanvas(board, title, { mode, scale: 1.5 })).toDataURL("image/jpeg", 0.92);
  const d = await api("/api/dash/pdf", { image: data, name: title });
  if (d.cancelled) return say(err, "");
  if (d.window === false) { download(d.pdf, d.name); return say(err, "보고서를 PDF로 내려받았습니다.", true); }
  say(err, `보고서를 저장했습니다: ${d.path}`, true);
}
