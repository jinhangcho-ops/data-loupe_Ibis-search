// 대시보드 템플릿: 분석 개요형·목표 달성형·모니터링형(카드 종류·자리·이름만, 데이터는 비어 있다). 직접 구성은 빈 격자다.
// 내 템플릿은 서버(settings dash_templates.json)에 kind·title·layout·colors만 저장한다(값·연결·조회는 저장하지 않는다). 12칸 격자 자리 {x, y, w, h}.
const shellCard = (kind, title, x, y, w, h, type = "") => ({ kind, title, layout: { x, y, w, h }, view: { type } });
const DASH_TEMPLATES = [
  { id: "overview", name: "분석 개요형", help: "핵심 지표 3개, 추이, 구성, 상세 표",
    cards: [shellCard("kpi", "핵심 지표 1", 0, 0, 4, 4), shellCard("kpi", "핵심 지표 2", 4, 0, 4, 4), shellCard("kpi", "핵심 지표 3", 8, 0, 4, 4),
            shellCard("chart", "추이", 0, 4, 8, 8, "line"), shellCard("table", "상세 표", 8, 4, 4, 8), shellCard("chart", "구성", 0, 12, 6, 8, "bar"), shellCard("summary", "한 줄 요약", 6, 12, 6, 8)] },
  { id: "goal", name: "목표 달성형", help: "달성률, 목표 추이, 진행 칸, 한 줄 문장",
    cards: [shellCard("donut_kpi", "달성률", 0, 0, 4, 8), shellCard("target_trend", "목표 추이", 4, 0, 8, 8), shellCard("stripes", "진행", 0, 8, 6, 4), shellCard("blocks", "채운 양", 6, 8, 6, 4), shellCard("sentence", "한 줄 문장", 0, 12, 12, 3)] },
  { id: "monitor", name: "모니터링형", help: "상태, 경고 목록, 배치도, 편차 표, 최근 행",
    cards: [shellCard("status", "상태", 0, 0, 4, 6), shellCard("alerts", "경고 목록", 4, 0, 8, 6), shellCard("map", "배치도", 0, 6, 6, 9), shellCard("deviation", "편차 표", 6, 6, 6, 9), shellCard("recent_rows", "최근 행", 0, 15, 12, 8)] },
  { id: "blank", name: "직접 구성", help: "빈 화면에서 카드를 하나씩 놓아요", cards: [] },
];

// 템플릿의 카드들을 새 카드(빈 틀)로. 서버 저장 모양 {id, kind, title, layout, colors, view, source, data}.
function templateCards(template, newId) {
  return template.cards.map(c => ({ id: newId(), kind: c.kind, title: c.title, layout: { ...c.layout }, colors: c.colors ?? { series: {}, parts: {}, order: [], parts_order: [] },
    view: { type: c.view?.type ?? "" }, source: {}, data: {} }));
}
// 지금 대시보드를 내 템플릿 모양(kind·title·layout·colors)으로
const toTemplate = (name, cards) => ({ name, cards: cards.map(({ kind, title, layout, colors }) => ({ kind, title, layout, colors })) });
