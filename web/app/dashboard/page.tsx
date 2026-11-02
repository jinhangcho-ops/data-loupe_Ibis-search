"use client";

import { useEffect, useState } from "react";

export default function DashboardPage() {
  const [msg, setMsg] = useState("상수를 불러오는 중…");

  useEffect(() => {
    fetch("/api/dash/constants")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((j) => setMsg(`상수 최상위 키 ${Object.keys(j).length}개를 받았어요`))
      .catch(() => setMsg("상수를 아직 못 받았어요"));
  }, []);

  return (
    <main style={{ padding: 16 }}>
      <h1>대시보드</h1>
      <p>{msg}</p>
    </main>
  );
}
