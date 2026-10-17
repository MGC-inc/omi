import Link from "next/link";

import type { ConversationView } from "@/lib/data";
import { CATEGORY_LABELS } from "@/lib/data";
import { formatLocal } from "@/lib/time";

export function ConversationCard({
  conversation,
  utcOffsetHours,
}: {
  conversation: ConversationView;
  utcOffsetHours: number;
}) {
  const open = conversation.actionItems.filter((a) => !a.completed).length;

  return (
    <Link
      href={`/conversations/${conversation.id}`}
      className="block rounded-xl border border-border px-4 py-3 transition-colors hover:border-accent"
    >
      <div className="flex items-start justify-between gap-2">
        <p className="flex min-w-0 items-baseline gap-2">
          <span aria-hidden>{conversation.emoji}</span>
          <span className="truncate font-medium text-foreground">
            {conversation.title}
          </span>
        </p>
        {conversation.notion ? (
          <span className="shrink-0 rounded-full bg-surface px-2 py-0.5 text-[11px] text-muted">
            Notion に保存済み
          </span>
        ) : null}
      </div>

      <p className="mt-1 line-clamp-2 text-sm text-muted">
        {conversation.overview || "（概要なし）"}
      </p>

      <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted">
        <span>{formatLocal(conversation.occurredAt, utcOffsetHours)}</span>
        {conversation.durationMinutes ? (
          <span>{conversation.durationMinutes}分</span>
        ) : null}
        <span>
          {CATEGORY_LABELS[conversation.category] ?? conversation.category}
        </span>
        {open > 0 ? <span className="text-accent">未完了 {open}件</span> : null}
      </div>
    </Link>
  );
}
