import { CategoryBars } from "@/components/CategoryBars";
import { SimpleBarChart } from "@/components/BarChart";
import { StatTile } from "@/components/StatTile";
import { env } from "@/lib/env";
import {
  CategoryCount,
  DailyPoint,
  WeeklyPoint,
  getCategoryBreakdown,
  getDailyStats,
  getOpenTasks,
  getWeeklyStats,
} from "@/lib/data";
import { ErrorPanel } from "@/components/ErrorPanel";

export default async function OverviewPage() {
  let daily: DailyPoint[];
  let weekly: WeeklyPoint[];
  let categories: CategoryCount[];
  let openTaskCount: number;
  try {
    const [dailyResult, weeklyResult, categoriesResult, openTasks] =
      await Promise.all([
        getDailyStats(14),
        getWeeklyStats(8),
        getCategoryBreakdown(),
        getOpenTasks(),
      ]);
    daily = dailyResult;
    weekly = weeklyResult;
    categories = categoriesResult;
    openTaskCount = openTasks.length;
  } catch (error) {
    return <ErrorPanel error={error} />;
  }

  const offset = env.utcOffsetHours();
  const today = daily[daily.length - 1];
  const last7 = daily.slice(-7);
  const week7Count = last7.reduce((sum, d) => sum + d.count, 0);
  const week7Minutes = last7.reduce((sum, d) => sum + d.minutes, 0);

  const dailyChart = daily.map((d) => ({ ...d, label: d.date.slice(5) }));
  const weeklyChart = weekly.map((w) => ({
    ...w,
    label: w.weekStart.slice(5),
  }));

  return (
    <div className="space-y-8">
      <section>
        <h1 className="mb-3 text-lg font-semibold text-foreground">概要</h1>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <StatTile
            label="今日の会話"
            value={`${today?.count ?? 0}件`}
            sub={`${today?.minutes ?? 0}分`}
          />
          <StatTile
            label="直近7日"
            value={`${week7Count}件`}
            sub={`${week7Minutes}分`}
          />
          <StatTile label="未完了タスク" value={`${openTaskCount}件`} />
          <StatTile
            label="集計対象期間"
            value={`${env.lookbackDays()}日`}
            sub={`UTC${offset >= 0 ? "+" : ""}${offset} 基準`}
          />
        </div>
      </section>

      <section>
        <h2 className="mb-2 text-sm font-semibold text-foreground">
          日別の会話数（直近14日）
        </h2>
        <div className="rounded-xl border border-border p-3">
          <SimpleBarChart data={dailyChart} dataKey="count" labelKey="label" />
        </div>
      </section>

      <section>
        <h2 className="mb-2 text-sm font-semibold text-foreground">
          週別の会話時間（分・直近8週）
        </h2>
        <div className="rounded-xl border border-border p-3">
          <SimpleBarChart
            data={weeklyChart}
            dataKey="minutes"
            labelKey="label"
            unit="分"
          />
        </div>
      </section>

      <section>
        <h2 className="mb-2 text-sm font-semibold text-foreground">
          カテゴリ内訳
        </h2>
        <div className="rounded-xl border border-border p-3">
          <CategoryBars counts={categories} />
        </div>
      </section>
    </div>
  );
}
