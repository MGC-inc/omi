/**
 * Server-only environment access. Every value here is read lazily (inside a
 * function, not at module scope) so that:
 *
 * 1. Importing this module never throws at build time, when no env vars are
 *    set yet — only calling one of these functions during a real request
 *    does. That keeps `next build` working on Vercel before env vars are
 *    configured, and keeps missing-config errors scoped to the one page that
 *    actually needed the value.
 * 2. Nothing here is ever sent to the client: this file has no "use client"
 *    and is only imported from Server Components, Server Actions, and route
 *    handlers.
 */

export class ConfigError extends Error {}

function required(name: string): string {
  const value = process.env[name];
  if (!value || !value.trim()) {
    throw new ConfigError(
      `${name} is not set. Add it in the Vercel project's Environment Variables (see README.md).`,
    );
  }
  return value.trim();
}

function optional(name: string): string | undefined {
  const value = process.env[name];
  return value && value.trim() ? value.trim() : undefined;
}

function optionalInt(name: string, fallback: number): number {
  const raw = optional(name);
  if (!raw) return fallback;
  const parsed = Number.parseInt(raw, 10);
  return Number.isFinite(parsed) ? parsed : fallback;
}

/** Accepts a raw 32-hex id, a dashed UUID, or a pasted Notion database URL. */
function normalizeNotionId(raw: string, name: string): string {
  let text = raw;
  if (text.includes("notion.so") || text.startsWith("http")) {
    const tail = text.split("?")[0].replace(/\/$/, "").split("/").pop() ?? text;
    const lastSegment = tail.split("-").pop() ?? tail;
    text = lastSegment.length === 32 ? lastSegment : tail;
  }
  const compact = text.replace(/-/g, "");
  if (compact.length !== 32 || !/^[0-9a-fA-F]{32}$/.test(compact)) {
    throw new ConfigError(
      `${name} does not look like a Notion database id (expected 32 hex characters, with or without dashes, or a notion.so URL). Got ${JSON.stringify(raw)}`,
    );
  }
  return compact;
}

function requiredNotionId(name: string): string {
  return normalizeNotionId(required(name), name);
}

function optionalNotionId(name: string): string | undefined {
  const raw = optional(name);
  return raw ? normalizeNotionId(raw, name) : undefined;
}

export const env = {
  omiApiKey: () => required("OMI_API_KEY"),
  omiApiBase: () => optional("OMI_API_BASE") ?? "https://api.omi.me",

  notionToken: () => required("NOTION_TOKEN"),
  notionDatabaseId: () => requiredNotionId("NOTION_DATABASE_ID"),
  // Optional: a dedicated tasks / sales-knowledge database, if one has been
  // set up (see integrations/meeting-digest's notion_tasks / notion_sales
  // sinks). Absent by default — the dashboard derives tasks and ideas from
  // the Omi API's own action_items/overview instead when these are unset.
  notionTasksDatabaseId: () => optionalNotionId("NOTION_TASKS_DATABASE_ID"),
  notionSalesDatabaseId: () => optionalNotionId("NOTION_SALES_DATABASE_ID"),

  // The dashboard's own login — a single shared password, not a user
  // database. Required: there is no "open" mode.
  dashboardPassword: () => required("DASHBOARD_PASSWORD"),
  // Signs the session cookie. Required and separate from the password so
  // that rotating the password doesn't require also regenerating this.
  sessionSecret: () => required("SESSION_SECRET"),

  utcOffsetHours: () => optionalInt("DASHBOARD_UTC_OFFSET_HOURS", 9),
  lookbackDays: () => optionalInt("DASHBOARD_LOOKBACK_DAYS", 60),

  // Optional: translates non-Japanese titles/overviews/action items into
  // Japanese (see lib/translate.ts). Absent by default — the dashboard shows
  // whatever language Omi/Notion returned, same as before this existed.
  geminiApiKey: () => optional("GEMINI_API_KEY"),
  translateModel: () => optional("TRANSLATE_MODEL") ?? "gemini-3.5-flash-lite",
  geminiApiBase: () => optional("GEMINI_API_BASE") ?? "https://generativelanguage.googleapis.com",
};
