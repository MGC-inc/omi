import { ConversationCard } from "@/components/ConversationCard";
import { ErrorPanel } from "@/components/ErrorPanel";
import { SearchBar } from "@/components/SearchBar";
import {
  CATEGORY_LABELS,
  ConversationView,
  searchConversations,
} from "@/lib/data";
import { env } from "@/lib/env";

export default async function ConversationsPage({
  searchParams,
}: {
  searchParams: Promise<{ q?: string; category?: string }>;
}) {
  const { q = "", category } = await searchParams;

  let results: ConversationView[];
  try {
    results = await searchConversations(q, category);
  } catch (error) {
    return <ErrorPanel error={error} />;
  }

  const offset = env.utcOffsetHours();

  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold text-foreground">会話</h1>
      <SearchBar
        basePath="/conversations"
        query={q}
        category={category}
        categories={Object.keys(CATEGORY_LABELS)}
      />
      <p className="text-xs text-muted">{results.length}件</p>
      <div className="space-y-2">
        {results.map((c) => (
          <ConversationCard
            key={c.id}
            conversation={c}
            utcOffsetHours={offset}
          />
        ))}
        {results.length === 0 ? (
          <p className="text-sm text-muted">該当する会話がありません。</p>
        ) : null}
      </div>
    </div>
  );
}
