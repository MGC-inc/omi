import { login } from "./actions";

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ next?: string; error?: string }>;
}) {
  const { next = "/", error } = await searchParams;

  return (
    <main className="flex min-h-screen items-center justify-center px-6">
      <div className="w-full max-w-sm">
        <h1 className="mb-1 text-xl font-semibold text-foreground">
          Omi Dashboard
        </h1>
        <p className="mb-6 text-sm text-muted">
          パスワードを入力してください。
        </p>

        <form action={login} className="space-y-3">
          <input type="hidden" name="next" value={next} />
          <input
            type="password"
            name="password"
            autoComplete="current-password"
            autoFocus
            required
            placeholder="パスワード"
            className="w-full rounded-lg border border-border bg-background px-4 py-3 text-base text-foreground outline-none focus:border-accent"
          />
          {error ? (
            <p className="text-sm text-red-600">
              パスワードが正しくありません。
            </p>
          ) : null}
          <button
            type="submit"
            className="w-full rounded-lg bg-accent px-4 py-3 text-base font-medium text-accent-contrast transition-opacity active:opacity-80"
          >
            ログイン
          </button>
        </form>
      </div>
    </main>
  );
}
