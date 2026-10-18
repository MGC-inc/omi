import { NextRequest, NextResponse } from "next/server";

import { SESSION_COOKIE, verifySessionToken } from "@/lib/auth";

// Node runtime (not the default Edge one) so this can share auth.ts's
// node:crypto-based HMAC with the login server action verbatim.
export const runtime = "nodejs";

const PUBLIC_PATHS = ["/login"];

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  if (
    PUBLIC_PATHS.some(
      (path) => pathname === path || pathname.startsWith(`${path}/`),
    )
  ) {
    return NextResponse.next();
  }

  const token = request.cookies.get(SESSION_COOKIE)?.value;
  if (verifySessionToken(token)) {
    return NextResponse.next();
  }

  const loginUrl = new URL("/login", request.url);
  loginUrl.searchParams.set("next", pathname);
  return NextResponse.redirect(loginUrl);
}

export const config = {
  // Everything except static assets and the favicon — this covers pages and
  // API routes alike, so /api/transcript/[id] is gated the same way.
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
