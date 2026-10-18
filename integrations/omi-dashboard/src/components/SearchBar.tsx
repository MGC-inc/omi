import Link from "next/link";

import { CATEGORY_LABELS } from "@/lib/data";

/**
 * A plain GET form + query-param links — no client-side state needed, and
 * it works identically with or without JS (a back/forward tap just changes
 * the URL, which the server re-renders from).
 */
export function SearchBar({
  basePath,
  query,
  category,
  categories,
}: {
  basePath: string;
  query: string;
  category?: string;
  categories: string[];
}) {
  return (
    <div className="space-y-3">
      <form method="get" action={basePath} className="flex gap-2">
        {category ? (
          <input type="hidden" name="category" value={category} />
        ) : null}
        <input
          type="search"
          name="q"
          defaultValue={query}
          placeholder="キーワードで検索（タイトル・概要・アクション）"
          className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground outline-none focus:border-accent"
        />
        <button
          type="submit"
          className="shrink-0 rounded-lg bg-accent px-4 py-2 text-sm font-medium text-accent-contrast"
        >
          検索
        </button>
      </form>

      <div className="flex flex-wrap gap-1.5">
        <Link
          href={query ? `${basePath}?q=${encodeURIComponent(query)}` : basePath}
          className={chipClass(!category)}
        >
          すべて
        </Link>
        {categories.map((c) => (
          <Link
            key={c}
            href={`${basePath}?category=${c}${query ? `&q=${encodeURIComponent(query)}` : ""}`}
            className={chipClass(category === c)}
          >
            {CATEGORY_LABELS[c] ?? c}
          </Link>
        ))}
      </div>
    </div>
  );
}

function chipClass(active: boolean): string {
  return active
    ? "rounded-full bg-accent px-3 py-1 text-xs font-medium text-accent-contrast"
    : "rounded-full border border-border px-3 py-1 text-xs text-muted hover:border-accent hover:text-foreground";
}
