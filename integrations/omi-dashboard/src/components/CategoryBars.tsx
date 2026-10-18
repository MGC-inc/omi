import type { CategoryCount } from "@/lib/data";

/**
 * Category breakdown as labeled magnitude bars, not a pie/donut. Identity
 * here comes from the text label beside each bar, not from color — there is
 * only one brand hue, so distinguishing five categories by color is not an
 * option, and a meter (fill = accent, track = a lighter step of the same
 * hue) is exactly the "sequential, one hue" job per the dataviz skill.
 */
export function CategoryBars({ counts }: { counts: CategoryCount[] }) {
  const max = Math.max(1, ...counts.map((c) => c.count));

  if (counts.length === 0) {
    return <p className="text-sm text-muted">データがありません。</p>;
  }

  return (
    <ul className="space-y-2">
      {counts.map((c) => (
        <li key={c.category} className="flex items-center gap-3">
          <span className="w-20 shrink-0 text-sm text-foreground">
            {c.label}
          </span>
          <span className="h-2 flex-1 overflow-hidden rounded-full bg-surface">
            <span
              className="block h-full rounded-full bg-accent"
              style={{ width: `${Math.max(4, (c.count / max) * 100)}%` }}
            />
          </span>
          <span className="w-8 shrink-0 text-right text-sm tabular-nums text-muted">
            {c.count}
          </span>
        </li>
      ))}
    </ul>
  );
}
