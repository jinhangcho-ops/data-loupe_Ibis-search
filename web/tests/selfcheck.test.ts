// 폴더마다 있는 node 자가 점검(__pure__.ts, assert가 틀리면 던진다)도 npm test로 돌린다(AC7)
import { it } from "vitest";

it("hooks/__pure__", async () => { await import("@/components/hooks/__pure__"); });
it("report/__pure__", async () => { await import("@/components/report/__pure__"); });
it("edit/menu/__pure__", async () => { await import("@/components/edit/menu/__pure__"); });
it("edit/panel/__pure__", async () => { await import("@/components/edit/panel/__pure__"); });
