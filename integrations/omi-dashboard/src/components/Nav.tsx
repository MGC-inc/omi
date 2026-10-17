"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { logout } from "@/app/logout-action";

const ITEMS = [
  { href: "/", label: "概要", icon: "📊" },
  { href: "/conversations", label: "会話", icon: "💬" },
  { href: "/tasks", label: "タスク", icon: "✅" },
  { href: "/ideas", label: "アイデア", icon: "💡" },
];

function isActive(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/";
  return pathname.startsWith(href);
}

export function Nav() {
  const pathname = usePathname();

  return (
    <>
      {/* Desktop: a top bar. */}
      <header className="hidden border-b border-border sm:block">
        <div className="mx-auto flex max-w-5xl items-center gap-6 px-6 py-3">
          <span className="text-sm font-semibold text-foreground">
            Omi Dashboard
          </span>
          <nav className="flex flex-1 gap-4">
            {ITEMS.map((item) => (
              <Link
                key={item.href}
                href={item.href}
                className={
                  isActive(pathname, item.href)
                    ? "text-sm font-medium text-accent"
                    : "text-sm font-medium text-muted hover:text-foreground"
                }
              >
                {item.label}
              </Link>
            ))}
          </nav>
          <form action={logout}>
            <button
              type="submit"
              className="text-sm text-muted hover:text-foreground"
            >
              ログアウト
            </button>
          </form>
        </div>
      </header>

      {/* Mobile: a fixed bottom tab bar — easier to reach with a thumb than a top bar. */}
      <nav className="fixed inset-x-0 bottom-0 z-10 flex border-t border-border bg-background sm:hidden">
        {ITEMS.map((item) => (
          <Link
            key={item.href}
            href={item.href}
            className="flex flex-1 flex-col items-center gap-0.5 py-2 text-xs"
          >
            <span className="text-lg" aria-hidden>
              {item.icon}
            </span>
            <span
              className={
                isActive(pathname, item.href)
                  ? "font-medium text-accent"
                  : "text-muted"
              }
            >
              {item.label}
            </span>
          </Link>
        ))}
      </nav>
    </>
  );
}
