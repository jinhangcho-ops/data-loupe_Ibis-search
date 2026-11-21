// B11 받기 설정(plans/dash-blocks.md "B11 세부 명세")의 화면 쪽 순수 계산(DOM 없음). 상수는 서버 dash_store.PULL_*(consts.dash.pull)를 받는다.
import type { Dashboard, Pull, PullConsts, PullTable } from "@/lib/types";

/** [SPECIFICATION] plans/dash-blocks.md#pullOf
 *  대시보드의 받기 설정(저장본에 없으면 consts의 처음 값 사본). */
export function pullOf(dash: Pick<Dashboard, "pull">, c: PullConsts): Pull {
  return dash.pull ?? structuredClone(c.default);
}

/** [SPECIFICATION] plans/dash-blocks.md#everySeconds
 *  갱신 주기를 초로(null = 단추를 누를 때만 = 0). */
export function everySeconds(every: Pull["every"], c: PullConsts): number {
  return every === null ? 0 : every.n * c.units[every.unit];
}

// 초를 "1시간"·"30분"·"45초" 글자로(서버 dash_refresh.minimum_text와 같다)
export const minimumText = (s: number): string => (s % 3600 === 0 ? `${s / 3600}시간` : s % 60 === 0 ? `${s / 60}분` : `${s}초`);

/** [SPECIFICATION] plans/dash-blocks.md#pullError
 *  받기 설정을 저장할 수 없는 이유 한 문장(없으면 ""). dbMin은 계산 방식 db일 때의 연결별 최소(초). 서버 check_pull·check_pull_every와 같은 문구. */
export function pullError(pull: Pull, tables: PullTable[], c: PullConsts, dbMin: number): string {
  const e = pull.every;
  if (e !== null && (!Number.isInteger(e.n) || e.n < 1 || !Object.hasOwn(c.units, e.unit))) {
    return "갱신 주기는 1 이상의 정수와 단위(초·분·시)로 적어 주세요.";
  }
  const secs = e === null ? 0 : everySeconds(e, c);
  if (e !== null && (secs < c.min_every || secs > c.max_every)) {
    return `갱신 주기는 ${c.min_every}초 이상 ${c.max_every / 3600}시 이하예요.`;
  }
  if (pull.calc === "db" && secs > 0 && secs < dbMin) {
    return `이 연결의 갱신 주기는 ${minimumText(dbMin)} 이상이어야 해요(계산 방식 DB).`;
  }
  if (pull.range === "new") {
    const t = tables.find((x) => !x.join && !pull.marks.some((m) => m.schema === x.schema && m.table === x.table));
    if (t) return `'${t.table}' 테이블의 새 줄을 알아볼 컬럼을 대시보드 설정에서 골라 주세요(없는 것만 받기).`;
  }
  return "";
}
