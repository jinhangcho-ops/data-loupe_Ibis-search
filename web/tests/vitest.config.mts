// 순수 함수 시험(vitest, 화면 없이 node에서): cd web && npx vitest run --config tests/vitest.config.mts
import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: { alias: { "@": fileURLToPath(new URL("..", import.meta.url)) } },
  test: { include: ["tests/**/*.test.{ts,tsx}"], root: fileURLToPath(new URL("..", import.meta.url)), environment: "node" },
});
