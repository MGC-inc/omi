import { ActionItemRow } from "@/components/ActionItemRow";
import { ErrorPanel } from "@/components/ErrorPanel";
import { ActionItemView, getOpenTasks } from "@/lib/data";
import { env } from "@/lib/env";

export default async function TasksPage() {
  let tasks: ActionItemView[];
  try {
    tasks = await getOpenTasks();
  } catch (error) {
    return <ErrorPanel error={error} />;
  }

  const offset = env.utcOffsetHours();

  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold text-foreground">未完了タスク</h1>
      <p className="text-xs text-muted">{tasks.length}件・期限が近い順</p>
      <ul className="space-y-2">
        {tasks.map((task, index) => (
          <ActionItemRow
            key={`${task.conversationId}-${index}`}
            item={task}
            utcOffsetHours={offset}
          />
        ))}
        {tasks.length === 0 ? (
          <p className="text-sm text-muted">未完了のタスクはありません。</p>
        ) : null}
      </ul>
    </div>
  );
}
