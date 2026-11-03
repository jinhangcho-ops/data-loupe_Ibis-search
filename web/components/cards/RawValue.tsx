"use client";
import type { ComputeResult } from "@/lib/types";

/** 아직 그리지 못하는 카드의 자리: 구한 결과(없으면 저장된 값)를 JSON 그대로 */
export function RawValue({ data, result }: { data: unknown; result?: ComputeResult }) {
  return <pre style={{ margin: 0, maxHeight: 240, overflow: "auto", fontSize: 12 }}>{JSON.stringify(result ?? { data }, null, 1)}</pre>;
}
