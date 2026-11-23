// (B7) 템플릿 목록 잇기와 템플릿 → 새 대시보드(섹션·조각) 만들기. 화면 없이 계산한다. 명세: plans/dash-blocks.md "B7 세부 명세".
import { freshColors, freshView } from "@/components/edit/menu/kinds";
import type { Card, CardKind, Section, Template } from "./types";

/** 이 화면이 아는 템플릿 형식 번호(서버 dash_store.TEMPLATE_FORMAT과 같다) */
export const TEMPLATE_FORMAT = 1;
/** 직접 구성(빈 화면). 파일이 아니라 조각이 없는 시작 모양이다 */
export const BLANK: Template = { format: TEMPLATE_FORMAT, id: "blank", name: "직접 구성", help: "빈 화면에서 카드를 하나씩 놓아요", pages: [], sections: [], pieces: [], unknown: [] };
/** 처음 골라 둘 기본 템플릿 id(저장소 templates/overview.json의 파일 이름) */
export const FIRST_PICK = "overview";
/** 내 템플릿을 기본 템플릿과 섞어 보일 때 id 앞·이름 뒤에 붙이는 글자, 설명이 없을 때의 설명 */
export const MY_PREFIX = "my:";
export const MY_SUFFIX = " (내 템플릿)";
export const MY_HELP = "저장해 둔 카드 배치";

/** [SPECIFICATION] plans/dash-blocks.md#allTemplates
 *  고르는 칸의 목록: 직접 구성 + 기본 템플릿 + 내 템플릿(id·이름·설명을 바꾼 사본). */
export function allTemplates(builtin: Template[], mine: Template[]): Template[] {
  return [BLANK, ...builtin, ...mine.map(t => ({ ...t, id: MY_PREFIX + t.id, name: t.name + MY_SUFFIX, help: t.help || MY_HELP }))];
}

/** [SPECIFICATION] plans/dash-blocks.md#templateDash
 *  템플릿 → 새 대시보드에 넣을 쪽·섹션·카드(새 id, 못 여는 종류의 조각은 뺌). */
export function templateDash(t: Template, newId: () => string): { pages: string[]; sections: Section[]; cards: Card[] } {
  const ids = new Map(t.sections.map(s => [s.id, newId()]));
  const sections = t.sections.map(s => ({ ...s, id: ids.get(s.id)!, layout: { ...s.layout }, view: { ...s.view } }));
  const skip = new Set(t.unknown);
  const cards = t.pieces.filter(p => !skip.has(p.kind)).map(p => ({
    id: newId(), kind: p.kind as CardKind, title: p.title, layout: { ...p.layout }, section: ids.get(p.section)!,
    colors: p.colors ? structuredClone(p.colors) : freshColors(),
    view: { ...freshView(p.kind as CardKind, p.view?.type ?? ""), ...structuredClone(p.view ?? {}) } as Card["view"],
    source: {}, data: {},
  }));
  return { pages: [...t.pages], sections, cards };
}
