import { ConfigError } from "@/lib/env";
import { OmiRateLimitedError } from "@/lib/omi";

/**
 * A readable panel for the two failure classes every data page can hit:
 * missing configuration (env vars not set yet) and a rate-limited Omi call.
 * Anything else still renders a message rather than taking down the page —
 * one bad conversation or a transient network error should not blank the
 * whole dashboard.
 */
export function ErrorPanel({ error }: { error: unknown }) {
  const message = error instanceof Error ? error.message : String(error);

  let title = "読み込みに失敗しました";
  if (error instanceof ConfigError) title = "設定が未完了です";
  else if (error instanceof OmiRateLimitedError)
    title = "Omi APIのレート制限に達しました";

  return (
    <div className="rounded-xl border border-border bg-surface p-4">
      <p className="text-sm font-medium text-foreground">{title}</p>
      <p className="mt-1 text-sm text-muted">{message}</p>
    </div>
  );
}
