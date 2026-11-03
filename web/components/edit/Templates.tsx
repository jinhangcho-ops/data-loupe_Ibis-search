"use client";
// N6 — 템플릿(옛 dash_templates.js·dashboard.html showTemplates·d-tpl-save): 기본 4종 + 내 템플릿(서버 dash_templates.json, 카드 종류·이름·자리·색만).
// 기본 4종·templateCards는 이 한 벌을 새 대시보드를 만드는 쪽(N4 PickDashboard)도 쓴다. onPick이 있으면 시작 모양을 고르는 칸을 보인다(처음에는 첫 것을 고른 것으로 알린다).
import { useEffect, useState } from "react";
import { deleteTemplate, listTemplates, saveTemplate } from "@/lib/api";
import { useDash } from "@/lib/store";
import type { Card, CardKind, ChartType, Template } from "@/lib/types";
import { freshView } from "./menu/kinds";

export type TemplatesProps = { onPick?: (t: Template) => void };
/** 기본 템플릿은 차트 종류(view.type)와 설명(help)을 더 가진다 */
export type BaseTemplate = Omit<Template, "cards"> & { help?: string; cards: (Template["cards"][number] & { view?: { type: ChartType } })[] };

const NO_COLORS = () => ({ series: {}, parts: {}, order: [], parts_order: [] });
const shell = (kind: CardKind, title: string, x: number, y: number, w: number, h: number, type: ChartType = "") => ({ kind, title, layout: { x, y, w, h }, colors: NO_COLORS(), view: { type } });
/** 기본 템플릿(옛 DASH_TEMPLATES): 분석 개요형·목표 달성형·모니터링형·직접 구성(빈 격자) */
export const TEMPLATES: BaseTemplate[] = [
  { id: "overview", name: "분석 개요형", help: "핵심 지표 3개, 추이, 구성, 상세 표",
    cards: [shell("kpi", "핵심 지표 1", 0, 0, 4, 4), shell("kpi", "핵심 지표 2", 4, 0, 4, 4), shell("kpi", "핵심 지표 3", 8, 0, 4, 4),
            shell("chart", "추이", 0, 4, 8, 8, "line"), shell("table", "상세 표", 8, 4, 4, 8), shell("chart", "구성", 0, 12, 6, 8, "bar"), shell("summary", "한 줄 요약", 6, 12, 6, 8)] },
  { id: "goal", name: "목표 달성형", help: "달성률, 목표 추이, 진행 칸, 한 줄 문장",
    cards: [shell("donut_kpi", "달성률", 0, 0, 4, 8), shell("target_trend", "목표 추이", 4, 0, 8, 8), shell("stripes", "진행", 0, 8, 6, 4), shell("blocks", "채운 양", 6, 8, 6, 4), shell("sentence", "한 줄 문장", 0, 12, 12, 3)] },
  { id: "monitor", name: "모니터링형", help: "상태, 경고 목록, 배치도, 편차 표, 최근 행",
    cards: [shell("status", "상태", 0, 0, 4, 6), shell("alerts", "경고 목록", 4, 0, 8, 6), shell("map", "배치도", 0, 6, 6, 9), shell("deviation", "편차 표", 6, 6, 6, 9), shell("recent_rows", "최근 행", 0, 15, 12, 8)] },
  { id: "blank", name: "직접 구성", help: "빈 화면에서 카드를 하나씩 놓아요", cards: [] },
];

/** 카드 id(옛 randomId: 16자 16진수) */
export const newCardId = () => [...crypto.getRandomValues(new Uint8Array(8))].map(b => b.toString(16).padStart(2, "0")).join("");
/** 템플릿의 카드들 → 새 카드(빈 틀). 서버 저장 모양 그대로(옛 templateCards). 대시보드의 template 칸은 직접 구성이면 "", 아니면 템플릿 이름 */
export const templateCards = (t: BaseTemplate, newId = newCardId): Card[] => t.cards.map(c => ({
  id: newId(), kind: c.kind, title: c.title, layout: { ...c.layout }, colors: c.colors ?? NO_COLORS(), view: freshView(c.kind, c.view?.type), source: {} as Card["source"], data: {} as Card["data"] }));

export function Templates({ onPick }: TemplatesProps) {
  const { state } = useDash();
  const [mine, setMine] = useState<Template[]>([]);
  const [picked, setPicked] = useState(TEMPLATES[0].id);
  const [name, setName] = useState("");
  const [note, setNote] = useState({ text: "", ok: false });
  useEffect(() => { listTemplates().then(d => setMine(d.templates)).catch(() => {}); }, []);
  const all: BaseTemplate[] = [...TEMPLATES, ...mine.map(t => ({ ...t, id: "my:" + t.id, name: `${t.name} (내 템플릿)`, help: "저장해 둔 카드 배치" }))];
  // 처음 보일 때 고른 것(첫 템플릿)을 알린다: 화면에 보이는 것과 부르는 쪽이 아는 것이 같게
  useEffect(() => { onPick?.(all[0]); }, []);  // eslint-disable-line react-hooks/exhaustive-deps
  const save = async () => {
    try {
      const cards = state.dash?.cards ?? [];
      if (!cards.length) throw new Error("카드가 하나 이상 있어야 템플릿으로 저장할 수 있어요.");
      setMine((await saveTemplate({ name, cards: cards.map(({ kind, title, layout, colors }) => ({ kind, title, layout, colors })) })).templates);
      setNote({ text: "내 템플릿으로 저장했어요. 카드 종류, 자리, 색, 이름만 담겼어요.", ok: true });
    } catch (e) { setNote({ text: (e as Error).message, ok: false }); }
  };
  const drop = async (id: string) => {
    try { setMine((await deleteTemplate(id)).templates); } catch (e) { setNote({ text: (e as Error).message, ok: false }); }
  };
  return (
    <div className="templates">
      {onPick && (
        <select value={picked} title="시작 모양. 카드 자리만 놓이고, 데이터는 카드마다 정해요" style={{ width: "auto" }}
          onChange={e => { setPicked(e.target.value); const t = all.find(x => x.id === e.target.value); if (t) onPick(t); }}>
          {all.map(t => <option key={t.id} value={t.id} title={t.help ?? ""}>{t.name}</option>)}
        </select>
      )}
      <div className="row">
        <input placeholder="템플릿 이름" value={name} onChange={e => setName(e.target.value)} />
        <button className="btn" title="카드 종류, 자리, 색, 이름만 저장해요. 값과 연결 정보는 담지 않아요" onClick={save}>내 템플릿으로 저장</button>
      </div>
      <div className={note.ok ? "hint" : "err"} style={{ margin: 0 }}>{note.text}</div>
      {mine.map(t => (
        <div key={t.id} className="row">
          <span className="hint" style={{ flex: 1 }}>{t.name} (카드 {t.cards.length}개)</span>
          <button className="ghost" onClick={() => drop(t.id)}>지우기</button>
        </div>
      ))}
    </div>
  );
}
