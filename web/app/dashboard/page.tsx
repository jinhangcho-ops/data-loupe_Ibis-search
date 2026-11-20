// 대시보드 보는 곳(/dashboard): 편집 도구 없이 보기만. 화면은 DashboardScreen이 그린다.
import { DashboardScreen } from "@/components/shell/DashboardScreen";

export default function DashboardPage() {
  return <DashboardScreen build={false} />;
}
