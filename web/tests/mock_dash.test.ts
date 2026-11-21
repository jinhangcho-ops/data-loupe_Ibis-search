// 시안 모양 머리 줄(Toolbar): [더 보기 ▾] 메뉴, 편집 밖에서도 열리는 대시보드 설정, 머리 줄·설정 창이 같이 쓰는 기간 칸, "N분 전 최신화" 글자.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { agoText } from "@/components/shell/PeriodBar";

const src = (path: string) => readFileSync(fileURLToPath(new URL("../" + path, import.meta.url)), "utf-8");
const bar = src("components/shell/Toolbar.tsx"), period = src("components/shell/PeriodBar.tsx");

describe("머리 줄 [더 보기 ▾]", () => {
  const from = bar.indexOf('<div className="drop" ref={more}'), menu = bar.slice(from, bar.indexOf("<SettingsDialog"));
  it("내보내기·대시보드 설정이 role=menu 안에 차례로 있고, 대시보드만 보기는 메뉴 밖 도구 줄에 있다", () => {
    expect(from).toBeGreaterThan(0);
    expect(menu).toContain('<div className="pop" role="menu">');
    const at = [">내보내기</button>", ">대시보드 설정</button>"].map(t => menu.indexOf(t));
    expect(at.every(i => i > 0)).toBe(true);
    expect([...at].sort((a, b) => a - b)).toEqual(at);
    expect(menu).toContain("setMenu(false); setReport(true);");
    expect(menu).not.toContain("d-only");
    expect(bar.slice(0, from)).toContain('<button className="btn" id="d-only" onClick={() => setDashOnly(true)}');
    expect(bar).not.toContain("내보내기 ▾");
  });
  it("[예시 데이터로 확인하기]는 메뉴 밖 도구 줄에 연결과 상관없이 늘 있고, 연결돼 있으면 한 번 더 눌러야 연결을 바꾼다", () => {
    const rows = bar.slice(0, from).split("\n"), line = rows.findIndex(l => l.includes('id="d-demo-bar"')), item = rows.slice(line, line + 2).join("\n");
    expect(line).toBeGreaterThan(0);
    expect(menu).not.toContain("예시 데이터");
    expect(rows[line].trimStart().startsWith("<button")).toBe(true);  // conn 조건으로 감싸지 않는다
    expect(item).toContain("if (conn && !sure) return setSure(true); setSure(false); demo();");  // 연결이 실패해도 단추 글자는 돌아온다
    expect(item).toContain("onBlur={() => setSure(false)}");
    expect(item).toContain('{sure ? "한 번 더 누르면 연결이 바뀌어요" : "예시 데이터로 확인하기"}');
    expect(item).toContain("연결이 예시 데이터로 바뀌어요");
    expect(bar).toContain("return () => { connectDemo().then(() => location.reload(), (e: Error) => dispatch({ type: \"error\", message: e.message })); };");
    const page = src("components/shell/DashboardScreen.tsx");
    expect(page).toContain("{!conn && <button id=\"d-demo\"");
    expect(page).toContain("onClick={e => { e.stopPropagation(); demo(); }}>예시 데이터로 확인하기</button>}");
    expect(page).not.toContain("둘러보기");
  });
  it("대시보드 설정은 편집 조건으로 감싸지 않는다", () => {
    const line = bar.split("\n").find(l => l.includes(">대시보드 설정</button>"))!;
    expect(line.trimStart().startsWith("<button")).toBe(true);
    expect(line).toContain("setMenu(false); setSettings(true);");
    expect(bar).not.toMatch(/state\.editing && <button[^\n]*대시보드 설정/);
    expect(menu).not.toContain("state.editing");
  });
  it("두 메뉴가 바깥 클릭·Esc 처리를 하나로 같이 쓴다", () => {
    expect(bar.match(/document\.addEventListener\("click"/g)).toHaveLength(1);
    expect(bar).toContain("!drop.current?.contains(t) && !more.current?.contains(t)");
    expect(bar.match(/onKeyDown=\{esc\}/g)).toHaveLength(2);
  });
});

describe("기간 칸과 최신화 시각", () => {
  it("설정 창의 PeriodField는 PeriodBar.tsx 한 곳(머리 줄에는 기간 칸이 없다)", () => {
    expect(bar).toContain('import { LastRefreshed } from "./PeriodBar";');
    expect(bar).not.toContain("d-per");
    expect(period).toContain("export function PeriodField(");
    expect(period.slice(period.indexOf("export function PeriodBar("))).toContain('<PeriodField id="dc-per" />');
    expect(period.match(/<Calendar /g)).toHaveLength(1);
    expect(period.match(/commitPeriod\(/g)).toHaveLength(3);  // 칸 벗어남·Enter·달력, 한 벌뿐
    expect(src("components/shell/SettingsDialog.tsx")).toContain("<PeriodBar />");
    for (const id of ["dc-asof", "dc-cmp", "dc-every", "dc-live", "dc-min"]) expect(period).toContain(`id="${id}"`);  // 나머지 5칸은 설정 창에 그대로
  });
  it("머리 줄에 <LastRefreshed />, 설정 창 안내와 같은 시각(useLastComputed)을 쓴다", () => {
    expect(bar).toContain("<LastRefreshed />");
    expect(period.match(/computed_at/g)).toHaveLength(1);
    expect(period.match(/useLastComputed\(\)/g)).toHaveLength(3);  // 정의 + LastRefreshed + PeriodBar
  });
  it("agoText: 방금·분·시간·일", () => {
    const now = new Date("2026-03-05T12:00:00").getTime();
    expect(agoText("2026-03-05T11:59:30", now)).toBe("방금 최신화");
    expect(agoText("2026-03-05T11:53:00", now)).toBe("7분 전 최신화");
    expect(agoText("2026-03-05T09:10:00", now)).toBe("2시간 전 최신화");
    expect(agoText("2026-03-02T12:00:00", now)).toBe("3일 전 최신화");
    expect(agoText("2026-03-05T12:00:20", now)).toBe("방금 최신화");  // 시계가 조금 어긋나도 음수가 아님
  });
});
