import { cache } from "react";

import { env } from "./env";
import { listConversations, OmiConversation } from "./omi";
import {
  NotionConversationEntry,
  queryConversationIndex,
  querySalesDatabase,
  queryTasksDatabase,
} from "./notion";
import {
  durationMinutes,
  localDateKey,
  localWeekKey,
  parseTimestamp,
} from "./time";
import { translateBatch } from "./translate";

export interface ActionItemView {
  description: string;
  completed: boolean;
  dueAt?: Date;
  owner?: string;
  context?: string;
  conversationId: string;
  conversationTitle: string;
}

export interface ConversationView {
  id: string;
  title: string;
  overview: string;
  category: string;
  emoji: string;
  startedAt?: Date;
  finishedAt?: Date;
  occurredAt?: Date;
  durationMinutes?: number;
  actionItems: ActionItemView[];
  sections: { heading: string; body: string }[];
  events: { title: string; start?: Date; durationMinutes: number }[];
  notion?: NotionConversationEntry;
}

export const CATEGORY_LABELS: Record<string, string> = {
  business: "商談・仕事",
  work: "仕事",
  technology: "技術",
  personal: "個人",
  other: "その他",
};

function toView(
  raw: OmiConversation,
  notionIndex: Map<string, NotionConversationEntry>,
): ConversationView {
  const structured = raw.structured ?? { title: "(無題)", overview: "" };
  const startedAt = parseTimestamp(raw.started_at);
  const finishedAt = parseTimestamp(raw.finished_at);
  const createdAt = parseTimestamp(raw.created_at);

  const actionItems: ActionItemView[] = (structured.action_items ?? []).map(
    (item) => ({
      description: item.description || "(内容なし)",
      completed: Boolean(item.completed),
      dueAt: parseTimestamp(item.due_at),
      owner: item.owner_name ?? undefined,
      context: item.context ?? undefined,
      conversationId: raw.id,
      conversationTitle: structured.title || "(無題)",
    }),
  );

  return {
    id: raw.id,
    title: structured.title || "(無題)",
    overview: structured.overview || "",
    category: structured.category || "other",
    emoji: structured.emoji || "🧠",
    startedAt,
    finishedAt,
    occurredAt: startedAt ?? createdAt ?? finishedAt,
    durationMinutes: durationMinutes(startedAt, finishedAt),
    actionItems,
    sections: (structured.sections ?? []).map((s) => ({
      heading: s.heading,
      body: s.body_markdown,
    })),
    events: (structured.events ?? []).map((e) => ({
      title: e.title,
      start: parseTimestamp(e.start),
      durationMinutes: e.duration ?? 30,
    })),
    notion: notionIndex.get(raw.id),
  };
}

/**
 * Every piece of display text in a conversation, gathered into one list so
 * translateBatch can resolve all of them with a single AI Gateway call per
 * request — never one call per conversation. Owner names are deliberately
 * left out: a person's name is not something to translate.
 */
function collectTranslatableText(views: ConversationView[]): string[] {
  const texts: string[] = [];
  for (const view of views) {
    texts.push(view.title, view.overview);
    for (const section of view.sections)
      texts.push(section.heading, section.body);
    for (const item of view.actionItems) {
      texts.push(item.description);
      if (item.context) texts.push(item.context);
    }
    for (const event of view.events) texts.push(event.title);
  }
  return texts;
}

function applyTranslations(
  views: ConversationView[],
  translations: Map<string, string>,
): ConversationView[] {
  const t = (text: string) => translations.get(text) ?? text;
  return views.map((view) => ({
    ...view,
    title: t(view.title),
    overview: t(view.overview),
    sections: view.sections.map((s) => ({
      heading: t(s.heading),
      body: t(s.body),
    })),
    actionItems: view.actionItems.map((item) => ({
      ...item,
      description: t(item.description),
      context: item.context ? t(item.context) : item.context,
      conversationTitle: t(item.conversationTitle),
    })),
    events: view.events.map((e) => ({ ...e, title: t(e.title) })),
  }));
}

/**
 * Everything the dashboard shows, for one request. `cache()` (React's
 * per-request memoization) means every page/component that calls this
 * during the same request shares one fetch instead of each re-querying Omi
 * and Notion separately.
 */
export const getConversations = cache(async (): Promise<ConversationView[]> => {
  const lookbackMs = env.lookbackDays() * 24 * 60 * 60 * 1000;
  const startDate = new Date(Date.now() - lookbackMs);

  // Omi is the core source; Notion only adds a "saved" badge/link on top of
  // it. A Notion outage or a missing NOTION_TOKEN must not take the whole
  // dashboard down with it — it degrades to "no badges", not an error page.
  const [raw, notionIndex] = await Promise.all([
    listConversations({ startDate }),
    queryConversationIndex().catch((error) => {
      console.error(
        "Notion conversation index unavailable, continuing without it:",
        error,
      );
      return new Map<string, NotionConversationEntry>();
    }),
  ]);

  const views = raw
    .map((item) => toView(item, notionIndex))
    .sort((a, b) => {
      const at = a.occurredAt?.getTime() ?? 0;
      const bt = b.occurredAt?.getTime() ?? 0;
      return bt - at;
    });

  // Translation is an addition on top of the already-built views, same as
  // the Notion badge above: a missing/unusable OIDC token, or any failure,
  // leaves the original-language text in place rather than breaking the page
  // (translateBatch itself never throws — see lib/translate.ts).
  const translations = await translateBatch(collectTranslatableText(views));
  return applyTranslations(views, translations);
});

export const getConversation = cache(
  async (id: string): Promise<ConversationView | undefined> => {
    const all = await getConversations();
    return all.find((c) => c.id === id);
  },
);

export const getOpenTasks = cache(async (): Promise<ActionItemView[]> => {
  const all = await getConversations();
  const items = all.flatMap((c) =>
    c.actionItems.filter((item) => !item.completed),
  );
  return items.sort((a, b) => {
    if (a.dueAt && b.dueAt) return a.dueAt.getTime() - b.dueAt.getTime();
    if (a.dueAt) return -1;
    if (b.dueAt) return 1;
    return 0;
  });
});

export interface DailyPoint {
  date: string;
  count: number;
  minutes: number;
}

export const getDailyStats = cache(
  async (days: number): Promise<DailyPoint[]> => {
    const all = await getConversations();
    const offset = env.utcOffsetHours();
    const buckets = new Map<string, DailyPoint>();

    const today = new Date();
    for (let i = days - 1; i >= 0; i--) {
      const d = new Date(today.getTime() - i * 24 * 60 * 60 * 1000);
      const key = localDateKey(d, offset);
      buckets.set(key, { date: key, count: 0, minutes: 0 });
    }

    for (const conversation of all) {
      if (!conversation.occurredAt) continue;
      const key = localDateKey(conversation.occurredAt, offset);
      const bucket = buckets.get(key);
      if (!bucket) continue; // outside the requested window
      bucket.count += 1;
      bucket.minutes += conversation.durationMinutes ?? 0;
    }

    return Array.from(buckets.values());
  },
);

export interface WeeklyPoint {
  weekStart: string;
  count: number;
  minutes: number;
}

export const getWeeklyStats = cache(
  async (weeks: number): Promise<WeeklyPoint[]> => {
    const all = await getConversations();
    const offset = env.utcOffsetHours();
    const buckets = new Map<string, WeeklyPoint>();

    const today = new Date();
    for (let i = weeks - 1; i >= 0; i--) {
      const d = new Date(today.getTime() - i * 7 * 24 * 60 * 60 * 1000);
      const key = localWeekKey(d, offset);
      buckets.set(key, { weekStart: key, count: 0, minutes: 0 });
    }

    for (const conversation of all) {
      if (!conversation.occurredAt) continue;
      const key = localWeekKey(conversation.occurredAt, offset);
      const bucket = buckets.get(key);
      if (!bucket) continue;
      bucket.count += 1;
      bucket.minutes += conversation.durationMinutes ?? 0;
    }

    return Array.from(buckets.values());
  },
);

export interface CategoryCount {
  category: string;
  label: string;
  count: number;
}

export const getCategoryBreakdown = cache(
  async (): Promise<CategoryCount[]> => {
    const all = await getConversations();
    const counts = new Map<string, number>();
    for (const conversation of all) {
      counts.set(
        conversation.category,
        (counts.get(conversation.category) ?? 0) + 1,
      );
    }
    return Array.from(counts.entries())
      .map(([category, count]) => ({
        category,
        label: CATEGORY_LABELS[category] ?? category,
        count,
      }))
      .sort((a, b) => b.count - a.count);
  },
);

export const searchConversations = cache(
  async (query: string, category?: string): Promise<ConversationView[]> => {
    const all = await getConversations();
    const needle = query.trim().toLowerCase();
    return all.filter((c) => {
      if (category && c.category !== category) return false;
      if (!needle) return true;
      return (
        c.title.toLowerCase().includes(needle) ||
        c.overview.toLowerCase().includes(needle) ||
        c.actionItems.some((item) =>
          item.description.toLowerCase().includes(needle),
        )
      );
    });
  },
);

/**
 * Rows from the optional dedicated Notion databases, for the ideas view.
 * Both are opt-in (empty when unconfigured) and, like the main index above,
 * a failure here degrades to "not shown" rather than an error page — these
 * are an addition to the ideas view, not something it depends on.
 */
export const getNotionExtras = cache(async () => {
  const [tasks, sales] = await Promise.all([
    queryTasksDatabase().catch((error) => {
      console.error(
        "Notion tasks database unavailable, continuing without it:",
        error,
      );
      return [];
    }),
    querySalesDatabase().catch((error) => {
      console.error(
        "Notion sales database unavailable, continuing without it:",
        error,
      );
      return [];
    }),
  ]);

  const translations = await translateBatch([
    ...tasks.map((row) => row.title),
    ...sales.flatMap((row) =>
      row.overview ? [row.title, row.overview] : [row.title],
    ),
  ]);
  const t = (text: string) => translations.get(text) ?? text;

  return {
    tasks: tasks.map((row) => ({ ...row, title: t(row.title) })),
    sales: sales.map((row) => ({
      ...row,
      title: t(row.title),
      overview: row.overview ? t(row.overview) : row.overview,
    })),
  };
});
