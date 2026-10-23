// 대시보드 지도 카드(geomap): 서버가 준 영역 경계(고리의 경도·위도 목록)와 점을 SVG 한 장으로 그린다. 확대·이동은 없고, 데이터 범위에 맞춘 웹 메르카토르 지도다.
// 영역은 값으로 색칠하고(코로플레스: 바탕 색의 투명도가 값에 따라 진해짐), 점은 작은 원으로 찍는다.
// [지도 배경(OpenStreetMap)]을 켰을 때만(view.geo.tiles) 범위에 맞는 줌의 타일 그림(tile.openstreetmap.org)을 깔고 "© OpenStreetMap contributors"를 적는다. 기본은 꺼짐(경계만).
// 위쪽은 값 → 모양 계산(메르카토르 투영, 타일 번호)이라 화면 없이 node로 시험할 수 있다. 아래 paintGeoMap은 dash_cards.js의 svg·stext·box·showValue·pickColor를 쓴다.

const OSM_TILES = "https://tile.openstreetmap.org", OSM_CREDIT = "© OpenStreetMap contributors", MAX_MERC_LAT = 85.0511287798;
const MAX_TILES = 16;  // 한 번에 받는 타일 수의 상한(보는 범위가 넓으면 줌을 낮춘다)
const mercX = lon => (lon + 180) / 360;  // 세계 좌표(0~1): 경도 → x
const mercY = lat => { const s = Math.sin(Math.max(-MAX_MERC_LAT, Math.min(MAX_MERC_LAT, lat)) * Math.PI / 180); return 0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI); };  // 위도 → y(위가 0)
// 범위 [최소 경도, 최소 위도, 최대 경도, 최대 위도]에 맞춘 그림 틀: 너비 width에 맞추고 높이가 maxH를 넘으면 높이에 맞춘다. pad는 가장자리 여백 비율.
function mapFrame(bounds, width, maxH = 520, pad = 0.06) {
  const [lo, la0, hi, la1] = bounds, tiny = 1e-6;
  let x0 = mercX(lo), x1 = mercX(hi), y0 = mercY(la1), y1 = mercY(la0);
  if (x1 - x0 < tiny) [x0, x1] = [x0 - 5e-5, x1 + 5e-5];  // 점 하나·세로선처럼 폭이 0이면 작은 폭을 준다
  if (y1 - y0 < tiny) [y0, y1] = [y0 - 5e-5, y1 + 5e-5];
  const px = (x1 - x0) * pad, py = (y1 - y0) * pad;
  [x0, x1, y0, y1] = [x0 - px, x1 + px, y0 - py, y1 + py];
  let scale = width / (x1 - x0);
  if ((y1 - y0) * scale > maxH) scale = maxH / (y1 - y0);
  const w = (x1 - x0) * scale, h = (y1 - y0) * scale;
  return { w, h, scale, x0, y0, x1, y1, project: (lon, lat) => [(mercX(lon) - x0) * scale, (mercY(lat) - y0) * scale] };
}
// 타일 줌: 타일 한 장이 그림에서 256~512 길이쯤 되게(scale / 256 의 log2 반올림), 타일 수가 MAX_TILES 이하가 되도록 낮춘다. 0~18.
function tileZoom(frame) {
  let z = Math.max(0, Math.min(18, Math.round(Math.log2(frame.scale / 256))));
  while (z > 0 && tileCount(frame, z) > MAX_TILES) z--;
  return z;
}
function tileRange(frame, z) {
  const n = 2 ** z, clamp = v => Math.max(0, Math.min(n - 1, v));
  const wide = frame.x0 < 0 && frame.x1 > 1;  // 세계 전체를 양쪽으로 넘으면 한 바퀴만, 한쪽만 넘으면(180 넘는 경도) 넘친 쪽을 감싸 이어 붙인다
  const edge = v => wide ? clamp(v) : v;
  return { x0: edge(Math.floor(frame.x0 * n)), x1: edge(Math.floor(frame.x1 * n - 1e-9)), y0: clamp(Math.floor(frame.y0 * n)), y1: clamp(Math.floor(frame.y1 * n - 1e-9)), n };
}
function tileCount(frame, z) { const r = tileRange(frame, z); return (r.x1 - r.x0 + 1) * (r.y1 - r.y0 + 1); }
// 줌 z에서 틀과 겹치는 타일들 [{ z, x, y, url, px, py, size }](px·py·size는 그림 안 위치·크기)
function tilesFor(frame, z) {
  const r = tileRange(frame, z), size = frame.scale / r.n, out = [];
  for (let y = r.y0; y <= r.y1; y++) for (let x = r.x0; x <= r.x1; x++) out.push({ z, x, y, url: `${OSM_TILES}/${z}/${((x % r.n) + r.n) % r.n}/${y}.png`, px: (x / r.n - frame.x0) * frame.scale, py: (y / r.n - frame.y0) * frame.scale, size });
  return out;
}
// 영역 값 → 칠 투명도(0.18~0.9). 값이 모두 같으면 가운데 정도. 빈 값은 null(칠하지 않음).
function fillLevels(values, floor = 0.18, top = 0.9) {
  const real = values.filter(v => typeof v === "number" && Number.isFinite(v)), lo = Math.min(...real), hi = Math.max(...real);
  return values.map(v => typeof v !== "number" || !Number.isFinite(v) ? null : hi === lo ? (floor + top) / 2 : floor + (top - floor) * (v - lo) / (hi - lo));
}
// 고리 하나(경도, 위도 번갈아 놓은 목록)를 SVG 경로 글자로
function ringPath(ring, project) {
  let d = "";
  for (let i = 0; i + 1 < ring.length; i += 2) { const [x, y] = project(ring[i], ring[i + 1]); d += (i ? "L" : "M") + x.toFixed(1) + " " + y.toFixed(1); }
  return d + "Z";
}

// ---------- 그리기 ----------
const GEO_W = 640;
function paintGeoMap(card, colors, board) {
  const v = card.data.values, view = card.view, geo = view.geo ?? {}, frame = mapFrame(v.bounds, GEO_W);
  const root = svg("svg", { viewBox: `0 0 ${frame.w.toFixed(1)} ${frame.h.toFixed(1)}`, preserveAspectRatio: "xMidYMid meet", style: "width:100%;height:100%;overflow:hidden" });
  const base = geo.color ?? colors.palette?.[0] ?? colors.series[0], fmt = cardFormat(card);
  if (geo.tiles) {  // 켰을 때만 타일을 받는다(꺼져 있으면 서버 요청이 없다). 못 받은 타일(오프라인 등)은 빼고 경계만 보인다.
    const back = svg("g", {});
    for (const t of tilesFor(frame, tileZoom(frame))) {
      const img = svg("image", { href: t.url, x: t.px.toFixed(1), y: t.py.toFixed(1), width: t.size.toFixed(1), height: t.size.toFixed(1), preserveAspectRatio: "none" });
      img.addEventListener("error", () => { img.remove(); if (!back.children.length) credit.remove(); });  // 하나도 못 받았으면 출처 표기도 뺀다
      back.append(img);
    }
    root.append(back);
  }
  const levels = fillLevels(v.regions.map(r => r.value)), shade = geo.tiles ? 0.7 : 1;
  v.regions.forEach((r, i) => {
    const own = card.colors?.parts?.[r.name], color = own ?? base;
    const path = svg("path", { d: r.rings.map(ring => ringPath(ring, frame.project)).join(""), "fill-rule": "evenodd", fill: own || levels[i] != null ? color : "none",
      "fill-opacity": own ? 0.85 : levels[i] == null ? 0 : (levels[i] * shade).toFixed(3), stroke: colors.text, "stroke-opacity": 0.55, "stroke-width": 0.8, "vector-effect": "non-scaling-stroke" },
      svg("title", {}, `${r.name}${r.value == null ? "" : " · " + showValue(r.value, fmt)}`));
    root.append(path);
  });
  if (v.points.length) {
    const dot = card.colors?.series?.["점"] ?? colors.palette?.[1] ?? colors.series[1];
    root.append(svg("g", { fill: dot, "fill-opacity": 0.75, stroke: colors.card, "stroke-width": 0.5 }, ...v.points.map(([lon, lat]) => { const [x, y] = frame.project(lon, lat); return svg("circle", { cx: x.toFixed(1), cy: y.toFixed(1), r: 2.6 }); })));
  }
  const nums = v.regions.map(r => r.value).filter(x => typeof x === "number");
  if (nums.length) {  // 범례: 옅은 색 → 진한 색과 가장 작은·큰 값
    const lo = Math.min(...nums), hi = Math.max(...nums), id = "geo-" + card.id, y = frame.h - 22;
    root.append(svg("defs", {}, svg("linearGradient", { id }, svg("stop", { offset: "0%", "stop-color": base, "stop-opacity": 0.18 }), svg("stop", { offset: "100%", "stop-color": base, "stop-opacity": 0.9 }))),
      svg("rect", { x: 8, y, width: 90, height: 8, fill: `url(#${id})`, stroke: colors.line }), stext(8, y - 3, v.label, { "font-size": 10, fill: colors.text }),
      stext(8, y + 19, showValue(lo, fmt), { "font-size": 10, fill: colors.text }), stext(98, y + 19, showValue(hi, fmt), { "font-size": 10, fill: colors.text, "text-anchor": "end" }));
  }
  const credit = stext(frame.w - 4, frame.h - 4, OSM_CREDIT, { "font-size": 9, fill: "#333", "text-anchor": "end", stroke: "#fff", "stroke-width": 2, "paint-order": "stroke" });
  if (geo.tiles) root.append(credit);
  return root;
}
