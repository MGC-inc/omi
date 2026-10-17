"use server";

import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { checkPassword, createSessionToken, SESSION_COOKIE } from "@/lib/auth";

export async function login(formData: FormData): Promise<void> {
  const password = String(formData.get("password") ?? "");
  const next = String(formData.get("next") ?? "/");
  const safeNext = next.startsWith("/") ? next : "/";

  if (!checkPassword(password)) {
    redirect(`/login?error=1&next=${encodeURIComponent(safeNext)}`);
  }

  const store = await cookies();
  store.set(SESSION_COOKIE, createSessionToken(), {
    httpOnly: true,
    secure: true,
    sameSite: "lax",
    path: "/",
    maxAge: 60 * 60 * 24 * 30,
  });

  redirect(safeNext);
}
