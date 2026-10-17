import { createHmac, timingSafeEqual } from "node:crypto";

import { env } from "./env";

/**
 * A minimal signed-cookie session — one shared password, not a user
 * database, because this dashboard has exactly one intended viewer. No
 * external auth provider, no stored sessions: the cookie itself carries an
 * expiry and an HMAC signature, verified on every request in middleware.ts.
 */

export const SESSION_COOKIE = "omi_dashboard_session";
const SESSION_TTL_MS = 30 * 24 * 60 * 60 * 1000; // 30 days

function sign(payload: string): string {
  return createHmac("sha256", env.sessionSecret())
    .update(payload)
    .digest("base64url");
}

export function createSessionToken(): string {
  const expiresAt = Date.now() + SESSION_TTL_MS;
  const payload = String(expiresAt);
  return `${payload}.${sign(payload)}`;
}

export function verifySessionToken(token: string | undefined | null): boolean {
  if (!token) return false;
  const [payload, signature] = token.split(".");
  if (!payload || !signature) return false;

  const expected = sign(payload);
  // Equal-length check first: timingSafeEqual throws on mismatched lengths,
  // and a length mismatch is not itself secret-dependent.
  const a = Buffer.from(signature);
  const b = Buffer.from(expected);
  if (a.length !== b.length || !timingSafeEqual(a, b)) return false;

  const expiresAt = Number.parseInt(payload, 10);
  return Number.isFinite(expiresAt) && Date.now() < expiresAt;
}

export function checkPassword(candidate: string): boolean {
  const expected = env.dashboardPassword();
  const a = Buffer.from(candidate);
  const b = Buffer.from(expected);
  // Constant-time compare — but only once lengths match; padding the
  // shorter buffer would leak length-equality differently, and length alone
  // is not sensitive here, so a short-circuit on it is fine.
  if (a.length !== b.length) return false;
  return timingSafeEqual(a, b);
}
