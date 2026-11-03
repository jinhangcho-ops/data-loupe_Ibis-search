// N6 — 카드·보고서 그림(옛 dash_report.js nodeToImage·inlineStyles·inlineTiles·paintedCanvas·reportCanvas, board.js themeColors·titledPicture).
// Recharts 차트도 SVG라 차트·SVG·HTML 카드를 모두 같은 길로 그린다: 화면의 카드 몸통을 복사해 계산된 스타일을 박고 SVG foreignObject로 직렬화 → Image → canvas.
// 그림 모드(밝게·어둡게)는 data-theme을 잠깐 바꿔 그 모드의 계산된 색을 박는다. 몸통은 화면 크기 그대로 그리고 그림 칸에 비율을 지켜 맞춘다(fitRect).
// 모두 브라우저에서만(단추를 눌렀을 때) 부른다.
import { fitRect, PICTURE_HEIGHT, REPORT, reportLayout, TITLE_BAND } from "./layout";
import type { Card } from "@/lib/types";

export type Colors = { card: string; head: string; ink: string; text: string; line: string };
const FONT = `"Segoe UI", "Malgun Gothic", sans-serif`;

/** 그 모드의 색(CSS 변수). 잠깐 바꿨다가 되돌린다(옛 themeColors) */
export function themeColors(mode: string): Colors {
  const root = document.documentElement, now = root.dataset.theme;
  root.dataset.theme = mode;
  const css = getComputedStyle(root), color = (name: string) => css.getPropertyValue(name).trim();
  const colors = { card: color("--card"), head: color("--bg"), ink: color("--text"), text: color("--muted"), line: color("--line") };
  if (now === undefined) delete root.dataset.theme; else root.dataset.theme = now;
  return colors;
}

// 그리는 데 쓰이는 속성만 박는다(전부 박으면 500행 표가 5초·57MB)
const INLINE_PROPS = ("display position top right bottom left float box-sizing width height min-width max-width min-height max-height z-index overflow-x overflow-y text-overflow visibility opacity transform transform-origin box-shadow aspect-ratio object-fit " +
  "margin-top margin-right margin-bottom margin-left padding-top padding-right padding-bottom padding-left " +
  "border-top-width border-right-width border-bottom-width border-left-width border-top-style border-right-style border-bottom-style border-left-style border-top-color border-right-color border-bottom-color border-left-color " +
  "border-top-left-radius border-top-right-radius border-bottom-right-radius border-bottom-left-radius border-collapse border-spacing table-layout " +
  "background-color background-image background-size background-position background-repeat color font-family font-size font-weight font-style line-height letter-spacing text-align text-decoration-line text-transform text-indent vertical-align white-space word-break overflow-wrap " +
  "flex-direction flex-wrap flex-grow flex-shrink flex-basis align-items align-self align-content justify-content justify-items order row-gap column-gap grid-template-columns grid-template-rows grid-column-start grid-column-end grid-row-start grid-row-end grid-auto-flow " +
  "color-scheme font-variant-numeric fill fill-opacity stroke stroke-width stroke-opacity stroke-dasharray stroke-linecap stroke-linejoin text-anchor dominant-baseline").split(" ");
function inlineStyles(source: Element, copy: Element) {
  const css = getComputedStyle(source);
  copy.setAttribute("style", INLINE_PROPS.map(name => { const v = css.getPropertyValue(name); return v === "" || (name !== "display" && (v === "none" || v === "normal")) ? "" : `${name}:${v};`; }).join(""));
  [...source.children].forEach((child, i) => { if (copy.children[i]) inlineStyles(child, copy.children[i]); });
}

// 지도 타일(<image href="https://…">): 직렬화한 그림은 바깥 그림을 못 불러오므로 data URL로 바꿔 넣는다. 못 받은 타일은 뺀다
function inlineTiles(root: Element) {
  return Promise.all([...root.querySelectorAll("image")].filter(n => /^https?:/.test(n.getAttribute("href") ?? "")).map(n => new Promise<void>(done => {
    const img = new Image(), giveUp = setTimeout(() => { n.remove(); done(); }, 8000);
    img.crossOrigin = "anonymous";
    img.onload = () => {
      clearTimeout(giveUp);
      const c = Object.assign(document.createElement("canvas"), { width: img.naturalWidth, height: img.naturalHeight });
      c.getContext("2d")!.drawImage(img, 0, 0);
      n.setAttribute("href", c.toDataURL("image/png"));
      done();
    };
    img.onerror = () => { clearTimeout(giveUp); n.remove(); done(); };
    img.src = n.getAttribute("href")!;
  })));
}

/** 화면의 요소 하나 → 그 크기의 Image. drop은 복사본에서 뺄 것(카드 머리 줄 등). 스타일은 지금 data-theme 기준 */
async function nodeToImage(node: HTMLElement, drop = "") {
  const width = Math.max(1, node.clientWidth), height = Math.max(1, node.clientHeight);
  const copy = node.cloneNode(true) as HTMLElement;
  inlineStyles(node, copy);
  if (drop) copy.querySelectorAll(drop).forEach(n => n.remove());
  Object.assign(copy.style, { position: "static", left: "0px", top: "0px", margin: "0px", width: width + "px", height: height + "px" });
  await inlineTiles(copy);
  const NS = "http://www.w3.org/2000/svg", wrap = document.createElementNS(NS, "svg"), fo = document.createElementNS(NS, "foreignObject");
  wrap.setAttribute("xmlns", NS);
  wrap.setAttribute("width", String(width));
  wrap.setAttribute("height", String(height));
  fo.setAttribute("width", "100%");
  fo.setAttribute("height", "100%");
  copy.setAttribute("xmlns", "http://www.w3.org/1999/xhtml");
  fo.append(copy);
  wrap.append(fo);
  const url = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(new XMLSerializer().serializeToString(wrap));
  return new Promise<HTMLImageElement>((resolve, reject) => {
    const img = new Image(width, height);
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error("카드를 그림으로 바꾸지 못했어요."));
    setTimeout(() => reject(new Error("카드를 그림으로 바꾸는 데 너무 오래 걸렸어요.")), 15000);
    img.src = url;
  });
}

/** 화면의 카드 상자(.bcard): data-id가 있으면 그것, 없으면 카드 순서대로 */
export function cardBox(cards: Card[], id: string) {
  return document.querySelector<HTMLElement>(`.board .bcard[data-id="${CSS.escape(id)}"]`) ?? document.querySelectorAll<HTMLElement>(".board .bcard")[cards.findIndex(c => c.id === id)] ?? null;
}
/** 카드 몸통의 그림(머리 줄·도구는 뺀다). 몸통 칸(.bbody)이 있으면 그것만 */
async function cardImage(box: HTMLElement) {
  const body = box.querySelector<HTMLElement>(".bbody");
  return body ? nodeToImage(body) : nodeToImage(box, ".bhead, .bextra, .btools");
}

/** 지정한 모드로 fn을 돈다(화면 data-theme을 한 번만 바꿨다 되돌린다) */
export async function inMode<T>(mode: string, fn: () => Promise<T>) {
  const root = document.documentElement, was = root.dataset.theme;
  root.dataset.theme = mode;
  try { return await fn(); } finally { if (was === undefined) delete root.dataset.theme; else root.dataset.theme = was; }
}

/** 카드 하나의 그림 파일(data URL): 위에 이름 줄, 배경은 그 모드의 카드 색. ratio가 있으면 세로 450 × 비율, 없으면 화면 크기. 배율 2 */
export async function cardPicture(box: HTMLElement, title: string, mode: string, type: "png" | "jpeg", ratio?: number) {
  const colors = themeColors(mode), img = await cardImage(box), scale = 2;
  const w = ratio ? Math.round(PICTURE_HEIGHT * ratio) : img.width, h = ratio ? PICTURE_HEIGHT - (title ? TITLE_BAND : 0) : img.height, band = title ? TITLE_BAND : 0;
  const out = Object.assign(document.createElement("canvas"), { width: w * scale, height: (h + band) * scale });
  const ctx = out.getContext("2d")!;
  ctx.fillStyle = colors.card;
  ctx.fillRect(0, 0, out.width, out.height);
  if (title) {
    ctx.fillStyle = colors.ink;
    ctx.font = `600 ${15 * scale}px ${FONT}`;
    ctx.textBaseline = "middle";
    ctx.fillText(title, 14 * scale, band * scale / 2, out.width - 28 * scale);
  }
  const f = fitRect(img.width, img.height, w, h);
  ctx.drawImage(img, f.x * scale, (band + f.y) * scale, f.w * scale, f.h * scale);
  return out.toDataURL("image/" + type);
}

/** 보고서 canvas(옛 reportCanvas): 제목 줄 + 카드마다 격자 자리의 상자(이름 줄 + 그림). 그리지 못한 카드는 이유 글만 */
export async function reportCanvas(cards: Card[], title: string, mode: string, scale = 2) {
  const colors = themeColors(mode), lay = reportLayout(cards);
  const out = Object.assign(document.createElement("canvas"), { width: lay.width * scale, height: lay.height * scale });
  const ctx = out.getContext("2d")!;
  const font = (px: number, weight = 400) => `${weight} ${px * scale}px ${FONT}`;
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
  const say = (text: string, x: number, y: number, w: number) => { ctx.fillStyle = colors.text; ctx.font = font(12); ctx.fillText(text, x + 14 * scale, y + (REPORT.band + 20) * scale, w - 28 * scale); };
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
    const bodyH = r.h - REPORT.band - 8, bodyW = r.w - 16, box = cardBox(cards, card.id);
    if (!box || bodyH < 20) { say(box ? "" : "화면에 없는 카드예요.", x, y, w); continue; }
    try {
      const img = await cardImage(box), f = fitRect(img.width, img.height, bodyW, bodyH);
      ctx.drawImage(img, x + (8 + f.x) * scale, y + (REPORT.band + f.y) * scale, f.w * scale, f.h * scale);
    } catch (e) { say((e as Error).message, x, y, w); }
  }
  return out;
}
