// Phase 5(plans/phase-5.md): 해시·UUID 컬럼을 그룹·집계 컬럼 목록에서 빼기(설정 hash_exclude, 서버 /api/diag/hashed가 컬럼 이름을 준다).

/** 목록에 남길 이름(names)과 뺀 이름(hidden, 안내·[다시 넣기]용). */
export type HashedPick = { names: string[]; hidden: string[] };

// [SPECIFICATION] plans/phase-5.md#withoutHashed
// 켜져 있으면 hashed에 든 이름을 빼고(restored에 든 것은 남긴다), 꺼져 있으면 그대로 돌려준다.
export function withoutHashed(names: string[], hashed: string[], on: boolean, restored: string[]): HashedPick {
  if (!on) return { names: [...names], hidden: [] };
  const hide = new Set(hashed.filter((n) => !restored.includes(n)));
  return { names: names.filter((n) => !hide.has(n)), hidden: names.filter((n) => hide.has(n)) };
}
