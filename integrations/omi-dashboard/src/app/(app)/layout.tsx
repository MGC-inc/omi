import { Nav } from "@/components/Nav";
import { logout } from "@/app/logout-action";

// Every page under this group reads live Omi/Notion data for one specific
// person — never pre-render or cache the page shell itself.
export const dynamic = "force-dynamic";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen flex-col">
      <Nav />
      {/* Logout lives in the desktop header (Nav.tsx); on mobile there is no
          room for it in the bottom tab bar, so it gets a small top-right link. */}
      <div className="flex justify-end px-4 pt-2 sm:hidden">
        <form action={logout}>
          <button type="submit" className="text-xs text-muted">
            ログアウト
          </button>
        </form>
      </div>
      <main className="mx-auto w-full max-w-5xl flex-1 px-4 pb-20 pt-2 sm:px-6 sm:pb-10 sm:pt-6">
        {children}
      </main>
    </div>
  );
}
