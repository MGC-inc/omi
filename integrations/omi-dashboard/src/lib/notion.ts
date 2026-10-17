import { env } from "./env";

/**
 * A thin, read-only client for the Notion API.
 *
 * This dashboard does NOT re-parse each Notion page's content blocks for its
 * main views — the Omi Developer API's list endpoint already returns the
 * same overview/action-items/sections structure, on a cheap, generous rate
 * limit (see omi.ts), so that is the primary source for stats, the
 * conversation feed, tasks, and the ideas view. Notion's job here is
 * narrower: it is the record of which conversations were judged worth
 * keeping (the "Omi 会話ログ" database), so the dashboard can show a
 * "Notion に保存済み" badge and link straight to the saved page.
 *
 * If a dedicated tasks / sales-knowledge database exists (see
 * integrations/meeting-digest's notion_tasks / notion_sales sinks),
 * NOTION_TASKS_DATABASE_ID / NOTION_SALES_DATABASE_ID can point at them and
 * their rows are merged in — see queryTasksDatabase / querySalesDatabase.
 * Neither exists yet as of this writing, so both are optional no-ops.
 */

const NOTION_API = "https://api.notion.com/v1";
const NOTION_VERSION = "2022-06-28";

export class NotionApiError extends Error {}

interface NotionRichText {
  plain_text: string;
}

interface NotionPage {
  id: string;
  url: string;
  properties: Record<string, unknown>;
}

async function notionRequest(path: string, body?: Record<string, unknown>) {
  let response: Response;
  try {
    response = await fetch(`${NOTION_API}${path}`, {
      method: body ? "POST" : "GET",
      headers: {
        Authorization: `Bearer ${env.notionToken()}`,
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
      },
      body: body ? JSON.stringify(body) : undefined,
      // Not cached across requests — React's cache() in data.ts dedupes
      // calls within one page view, which is all a personal dashboard needs.
      cache: "no-store",
      // An upstream that hangs must not hang the page forever.
      signal: AbortSignal.timeout(15000),
    });
  } catch (cause) {
    throw new NotionApiError(
      `Notion API request failed: ${(cause as Error).message}`,
    );
  }
  if (!response.ok) {
    const text = await response.text().catch(() => "");
    throw new NotionApiError(
      `Notion API ${path} returned HTTP ${response.status}: ${text.slice(0, 300)}`,
    );
  }
  return response.json();
}

function text(
  value: unknown,
  path: (keyof NotionRichText)[] = ["plain_text"],
): string {
  if (!Array.isArray(value)) return "";
  return value
    .map((run) => {
      let node: unknown = run;
      for (const key of path) node = (node as Record<string, unknown>)?.[key];
      return typeof node === "string" ? node : "";
    })
    .join("");
}

export interface NotionConversationEntry {
  omiId: string;
  url: string;
  minutesReviewStatus?: string; // "議事録化" — 不要 / 要議事録 / 議事録化済み
}

/** Omi ID -> the saved Notion page for it, for every row in the main log. */
export async function queryConversationIndex(): Promise<
  Map<string, NotionConversationEntry>
> {
  const index = new Map<string, NotionConversationEntry>();
  let cursor: string | undefined;

  do {
    const payload = await notionRequest(
      `/databases/${env.notionDatabaseId()}/query`,
      {
        page_size: 100,
        start_cursor: cursor,
      },
    );

    const results = (payload.results ?? []) as NotionPage[];
    for (const page of results) {
      const props = page.properties as Record<
        string,
        { rich_text?: unknown; select?: { name?: string } }
      >;
      const omiId = text(props["Omi ID"]?.rich_text);
      if (!omiId) continue;
      index.set(omiId, {
        omiId,
        url: page.url,
        minutesReviewStatus: props["議事録化"]?.select?.name,
      });
    }

    cursor = payload.has_more ? payload.next_cursor : undefined;
  } while (cursor);

  return index;
}

export interface NotionTaskRow {
  title: string;
  status?: string;
  due?: string;
  owner?: string;
  conversationId?: string;
  pageUrl: string;
}

/** Rows from the optional dedicated tasks database, if one is configured. */
export async function queryTasksDatabase(): Promise<NotionTaskRow[]> {
  const databaseId = env.notionTasksDatabaseId();
  if (!databaseId) return [];

  const rows: NotionTaskRow[] = [];
  let cursor: string | undefined;

  do {
    const payload = await notionRequest(`/databases/${databaseId}/query`, {
      page_size: 100,
      start_cursor: cursor,
    });

    const results = (payload.results ?? []) as NotionPage[];
    for (const page of results) {
      const props = page.properties as Record<
        string,
        {
          title?: unknown;
          select?: { name?: string };
          date?: { start?: string };
          rich_text?: unknown;
        }
      >;
      const titleColumn = Object.values(props).find((p) =>
        Array.isArray((p as { title?: unknown }).title),
      );
      rows.push({
        title: text((titleColumn as { title?: unknown })?.title) || "(無題)",
        status: props["Status"]?.select?.name,
        due: props["Due"]?.date?.start,
        owner: text(props["Owner"]?.rich_text) || undefined,
        conversationId:
          text(props["Omi Conversation ID"]?.rich_text) || undefined,
        pageUrl: page.url,
      });
    }

    cursor = payload.has_more ? payload.next_cursor : undefined;
  } while (cursor);

  return rows;
}

export interface NotionSalesRow {
  title: string;
  overview?: string;
  pageUrl: string;
  date?: string;
}

/** Rows from the optional dedicated sales-knowledge database, if configured. */
export async function querySalesDatabase(): Promise<NotionSalesRow[]> {
  const databaseId = env.notionSalesDatabaseId();
  if (!databaseId) return [];

  const rows: NotionSalesRow[] = [];
  let cursor: string | undefined;

  do {
    const payload = await notionRequest(`/databases/${databaseId}/query`, {
      page_size: 100,
      start_cursor: cursor,
    });

    const results = (payload.results ?? []) as NotionPage[];
    for (const page of results) {
      const props = page.properties as Record<
        string,
        { title?: unknown; date?: { start?: string } }
      >;
      const titleColumn = Object.values(props).find((p) =>
        Array.isArray((p as { title?: unknown }).title),
      );
      rows.push({
        title: text((titleColumn as { title?: unknown })?.title) || "(無題)",
        pageUrl: page.url,
        date: props["Date"]?.date?.start,
      });
    }

    cursor = payload.has_more ? payload.next_cursor : undefined;
  } while (cursor);

  return rows;
}
