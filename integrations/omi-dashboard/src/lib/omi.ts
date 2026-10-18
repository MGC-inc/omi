import { env } from "./env";

/**
 * A thin, read-only client for the Omi Developer API.
 *
 * Rate limits are per key, per hour (see the Omi backend's
 * rate_limit_config.py, and integrations/meeting-digest's README for the
 * numbers): conversation reads total 60/hr, and reads that include a
 * transcript are capped at 25/hr — the scarce budget. The LIST endpoint
 * already returns each conversation's full `structured` block (title,
 * overview, category, sections, action_items, events) even without a
 * transcript, so almost everything this dashboard needs — stats, the
 * conversation feed, open tasks, the "ideas" view — comes from list calls on
 * the cheap 60/hr budget. Only an explicit "show transcript" fetches a
 * single conversation's transcript, and that is the only place the 25/hr
 * budget is spent (see getConversationWithTranscript).
 */

export class OmiApiError extends Error {
  constructor(
    message: string,
    public status?: number,
  ) {
    super(message);
  }
}

export class OmiRateLimitedError extends OmiApiError {}

export interface OmiActionItem {
  description: string;
  completed: boolean;
  due_at?: string | null;
  owner_name?: string | null;
  context?: string | null;
}

export interface OmiSection {
  heading: string;
  body_markdown: string;
}

export interface OmiEvent {
  title: string;
  description: string;
  start?: string | null;
  duration?: number | null;
}

export interface OmiTranscriptSegment {
  id?: string;
  text: string;
  speaker?: string | null;
  is_user?: boolean;
  start?: number;
  end?: number;
}

export interface OmiStructured {
  title: string;
  overview: string;
  emoji?: string;
  category?: string;
  sections?: OmiSection[];
  action_items?: OmiActionItem[];
  events?: OmiEvent[];
}

export interface OmiConversation {
  id: string;
  created_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  language?: string | null;
  source?: string | null;
  structured?: OmiStructured;
  transcript_segments?: OmiTranscriptSegment[];
}

interface ListParams {
  limit?: number;
  offset?: number;
  startDate?: Date;
  endDate?: Date;
  includeTranscript?: boolean;
}

async function omiFetch(path: string, searchParams: Record<string, string>) {
  const url = new URL(`${env.omiApiBase()}/v1/dev/user${path}`);
  for (const [key, value] of Object.entries(searchParams)) {
    if (value !== undefined && value !== "") url.searchParams.set(key, value);
  }

  let response: Response;
  try {
    response = await fetch(url, {
      headers: { Authorization: `Bearer ${env.omiApiKey()}` },
      // Every route here is already force-dynamic (see the (app) layout),
      // and React's cache() (see data.ts) dedupes repeated calls within one
      // request — so there is no need for Next's own Data Cache on top of
      // that, and one less moving part to interact with.
      cache: "no-store",
      // An upstream that hangs must not hang the page forever — a clear,
      // fast failure is strictly better than no response at all.
      signal: AbortSignal.timeout(15000),
    });
  } catch (cause) {
    throw new OmiApiError(
      `Omi API request failed: ${(cause as Error).message}`,
    );
  }

  if (response.status === 429) {
    throw new OmiRateLimitedError(
      "Omi API rate limit reached. Try again later.",
      429,
    );
  }
  if (!response.ok) {
    const body = await response.text().catch(() => "");
    throw new OmiApiError(
      `Omi API returned HTTP ${response.status}: ${body.slice(0, 300)}`,
      response.status,
    );
  }
  return response.json();
}

const MAX_LIST_PAGES = 20;

/**
 * Pages through the Omi conversation list for a window, transcripts
 * excluded. Not cached across requests — see data.ts's getConversations,
 * which wraps this in React's cache() so one dashboard page view makes one
 * call regardless of how many components read the result.
 */
export async function listConversations({
  limit = 100,
  startDate,
  endDate,
  includeTranscript = false,
}: ListParams = {}): Promise<OmiConversation[]> {
  const collected: OmiConversation[] = [];
  const seen = new Set<string>();

  for (let page = 0; page < MAX_LIST_PAGES; page++) {
    const batch = (await omiFetch("/conversations", {
      limit: String(limit),
      offset: String(page * limit),
      start_date: startDate?.toISOString() ?? "",
      end_date: endDate?.toISOString() ?? "",
      include_transcript: String(includeTranscript),
    })) as OmiConversation[];

    if (!Array.isArray(batch) || batch.length === 0) break;
    for (const item of batch) {
      if (item?.id && !seen.has(item.id)) {
        seen.add(item.id);
        collected.push(item);
      }
    }
    if (batch.length < limit) break;
  }

  return collected;
}

/**
 * One conversation, with its transcript. This is the only call in this
 * client that spends the scarce 25/hr transcript budget — it is fetched
 * only when TranscriptLoader explicitly asks for it, never on every page
 * view of a conversation's detail page.
 */
export async function getConversationWithTranscript(
  id: string,
): Promise<OmiConversation> {
  return omiFetch(`/conversations/${encodeURIComponent(id)}`, {
    include_transcript: "true",
  });
}
