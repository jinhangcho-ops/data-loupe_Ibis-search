"use client";
import type { ComputeResult } from "@/lib/types";

/** 아직 그리지 못하는 카드의 자리: 구한 결과(없으면 저장된 값)를 JSON 그대로 */
export function RawValue({ data, result }: { data: unknown; result?: ComputeResult }) {
  return <pre className="m-0 max-h-[15rem] overflow-auto [font-size:.75rem]">{JSON.stringify(result ?? { data }, null, 1)}</pre>;
}
