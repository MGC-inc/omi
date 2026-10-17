import { notFound } from "next/navigation";
import Link from "next/link";

import { ErrorPanel } from "@/components/ErrorPanel";
import { TranscriptLoader } from "@/components/TranscriptLoader";
import { CATEGORY_LABELS, getConversation } from "@/lib/data";
import { env } from "@/lib/env";
import { formatLocal } from "@/lib/time";

export default async function ConversationDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;

  // notFound() throws a special Next.js signal that must reach the nearest
  // not-found boundary unhandled — it is fetched outside the try/catch below
  // so a genuine 404 is never mistaken for a data-fetch error.
  let conversation;
  try {
    conversation = await getConversation(id);
  } catch (error) {
    return <ErrorPanel error={error} />;
  }
  if (!conversation) notFound();

  const offset = env.utcOffsetHours();

  return (
    <div className="space-y-5">
      <div>
        <Link
          href="/conversations"
          className="text-sm text-muted hover:text-accent"
        >
          ← 会話一覧
        </Link>
      </div>

      <div>
        <h1 className="text-lg font-semibold text-foreground">
          <span aria-hidden className="mr-1">
            {conversation.emoji}
          </span>
          {conversation.title}
        </h1>
        <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted">
          <span>{formatLocal(conversation.occurredAt, offset)}</span>
          {conversation.durationMinutes ? (
            <span>{conversation.durationMinutes}分</span>
          ) : null}
          <span>
            {CATEGORY_LABELS[conversation.category] ?? conversation.category}
          </span>
          {conversation.notion ? (
            <a
              href={conversation.notion.url}
              target="_blank"
              rel="noreferrer"
              className="text-accent hover:underline"
            >
              Notion で開く
            </a>
          ) : null}
        </div>
      </div>

      {conversation.overview ? (
        <section>
          <h2 className="mb-1 text-sm font-semibold text-foreground">概要</h2>
          <p className="whitespace-pre-wrap text-sm text-foreground">
            {conversation.overview}
          </p>
        </section>
      ) : null}

      {conversation.sections.map((section, index) => (
        <section key={index}>
          <h2 className="mb-1 text-sm font-semibold text-foreground">
            {section.heading}
          </h2>
          <p className="whitespace-pre-wrap text-sm text-foreground">
            {section.body}
          </p>
        </section>
      ))}

      {conversation.actionItems.length > 0 ? (
        <section>
          <h2 className="mb-1 text-sm font-semibold text-foreground">
            アクションアイテム
          </h2>
          <ul className="space-y-1">
            {conversation.actionItems.map((item, index) => (
              <li key={index} className="flex items-start gap-2 text-sm">
                <span
                  aria-hidden
                  className={`mt-0.5 h-3.5 w-3.5 shrink-0 rounded border ${
                    item.completed ? "border-accent bg-accent" : "border-border"
                  }`}
                />
                <span
                  className={
                    item.completed
                      ? "text-muted line-through"
                      : "text-foreground"
                  }
                >
                  {item.description}
                  {item.owner ? ` （担当: ${item.owner}）` : ""}
                  {item.dueAt
                    ? ` （期限: ${formatLocal(item.dueAt, offset, false)}）`
                    : ""}
                </span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {conversation.events.length > 0 ? (
        <section>
          <h2 className="mb-1 text-sm font-semibold text-foreground">予定</h2>
          <ul className="space-y-1 text-sm text-foreground">
            {conversation.events.map((event, index) => (
              <li key={index}>
                {event.title} — {formatLocal(event.start, offset)}（
                {event.durationMinutes}分）
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <section>
        <h2 className="mb-1 text-sm font-semibold text-foreground">
          文字起こし
        </h2>
        <TranscriptLoader conversationId={conversation.id} />
      </section>
    </div>
  );
}
