import Link from "next/link";

import type { ActionItemView } from "@/lib/data";
import { formatLocal } from "@/lib/time";

export function ActionItemRow({
  item,
  utcOffsetHours,
}: {
  item: ActionItemView;
  utcOffsetHours: number;
}) {
  return (
    <li className="flex items-start gap-3 rounded-lg border border-border px-3 py-2.5">
      <span
        aria-hidden
        className={`mt-0.5 h-4 w-4 shrink-0 rounded border ${
          item.completed ? "border-accent bg-accent" : "border-border"
        }`}
      />
      <div className="min-w-0 flex-1">
        <p
          className={
            item.completed
              ? "text-sm text-muted line-through"
              : "text-sm text-foreground"
          }
        >
          {item.description}
        </p>
        <div className="mt-0.5 flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-muted">
          {item.owner ? <span>担当: {item.owner}</span> : null}
          {item.dueAt ? (
            <span>期限: {formatLocal(item.dueAt, utcOffsetHours, false)}</span>
          ) : null}
          <Link
            href={`/conversations/${item.conversationId}`}
            className="text-accent hover:underline"
          >
            {item.conversationTitle}
          </Link>
        </div>
      </div>
    </li>
  );
}
