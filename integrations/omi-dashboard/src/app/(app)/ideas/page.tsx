import { ErrorPanel } from "@/components/ErrorPanel";
import { SearchBar } from "@/components/SearchBar";
import {
  CATEGORY_LABELS,
  ConversationView,
  getNotionExtras,
  searchConversations,
} from "@/lib/data";
import { env } from "@/lib/env";
import type { NotionSalesRow } from "@/lib/notion";
import { formatLocal } from "@/lib/time";

/**
 * "アイデア・決定事項・営業メモ" — there is no dedicated idea/sales-note
 * property in the Omi API or the Notion log today, so this view is each
 * conversation's own overview + sections (where the actual ideas and
 * decisions already live) as a feed, newest first. If a dedicated
 * sales-knowledge database is ever configured (NOTION_SALES_DATABASE_ID —
 * see integrations/meeting-digest's notion_sales sink), its rows are shown
 * above the feed instead of being silently ignored.
 */
export default async function IdeasPage({
  searchParams,
}: {
  searchParams: Promise<{ q?: string; category?: string }>;
}) {
  const { q = "", category } = await searchParams;

  let results: ConversationView[];
  let sales: NotionSalesRow[];
  try {
    const [resultsValue, extras] = await Promise.all([
      searchConversations(q, category),
      getNotionExtras(),
    ]);
    results = resultsValue;
    sales = extras.sales;
  } catch (error) {
    return <ErrorPanel error={error} />;
  }

  const offset = env.utcOffsetHours();
  const feed = results.filter((c) => c.overview || c.sections.length > 0);

  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold text-foreground">
        アイデア・決定事項
      </h1>
      <SearchBar
        basePath="/ideas"
        query={q}
        category={category}
        categories={Object.keys(CATEGORY_LABELS)}
      />

      {sales.length > 0 ? (
        <section>
          <h2 className="mb-2 text-sm font-semibold text-foreground">
            営業ナレッジDB
          </h2>
          <ul className="space-y-2">
            {sales.map((row, index) => (
              <li
                key={index}
                className="rounded-lg border border-border px-3 py-2"
              >
                <a
                  href={row.pageUrl}
                  target="_blank"
                  rel="noreferrer"
                  className="text-sm font-medium text-accent hover:underline"
                >
                  {row.title}
                </a>
                {row.overview ? (
                  <p className="mt-1 text-sm text-muted">{row.overview}</p>
                ) : null}
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <div className="space-y-3">
        {feed.map((c) => (
          <article
            key={c.id}
            className="rounded-xl border border-border px-4 py-3"
          >
            <div className="flex items-baseline justify-between gap-2">
              <h3 className="font-medium text-foreground">
                <span aria-hidden className="mr-1">
                  {c.emoji}
                </span>
                {c.title}
              </h3>
              <span className="shrink-0 text-xs text-muted">
                {formatLocal(c.occurredAt, offset, false)}
              </span>
            </div>
            <p className="mt-1 text-xs text-muted">
              {CATEGORY_LABELS[c.category] ?? c.category}
            </p>
            {c.overview ? (
              <p className="mt-2 whitespace-pre-wrap text-sm text-foreground">
                {c.overview}
              </p>
            ) : null}
            {c.sections.map((section, index) => (
              <div key={index} className="mt-2">
                <p className="text-xs font-medium text-muted">
                  {section.heading}
                </p>
                <p className="whitespace-pre-wrap text-sm text-foreground">
                  {section.body}
                </p>
              </div>
            ))}
          </article>
        ))}
        {feed.length === 0 ? (
          <p className="text-sm text-muted">該当する会話がありません。</p>
        ) : null}
      </div>
    </div>
  );
}
