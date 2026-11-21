// 대시보드 만드는 곳(/dashboard/build): 늘 편집 상태. 화면은 DashboardScreen이 그린다.
import { DashboardScreen } from "@/components/shell/DashboardScreen";

export default function DashboardBuildPage() {
  return <DashboardScreen build />;
}
