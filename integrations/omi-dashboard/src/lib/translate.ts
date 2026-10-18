import { env } from "./env";

/**
 * Translates whatever isn't already Japanese, using Gemini — the same
 * provider as integrations/meeting-digest's llm.py, called directly over
 * its REST API (no SDK dependency needed for one call shape).
 *
 * Translation is an enhancement, not a hard dependency: a missing
 * GEMINI_API_KEY, or any failure calling Gemini, falls back to the original
 * text rather than breaking the page — the same graceful-degradation stance
 * already used for Notion in notion.ts/data.ts.
 *
 * Caching: translated text is content-addressed (keyed by a hash of the
 * source string, not by conversation) in an in-memory Map that lives for as
 * long as this server instance stays warm. That is "best effort", not
 * durable — there is no persistent store in this project to put it in, and
 * a cache miss only ever costs one extra Gemini call, never a wrong answer.
 * It does mean the same sentence, even reused across different
 * conversations, is translated once rather than once per occurrence, and
 * repeat views of the same conversation on a warm instance cost nothing.
 */

const DEFAULT_SYSTEM_PROMPT = `\
あなたは翻訳者です。入力は文字列の配列です。各要素を自然な日本語に翻訳してください。

規則:
1. 意味を正確に保ち、情報を追加したり省略したりしないこと。推測で言葉を補わないこと。
2. すでに日本語の要素、固有名詞・製品名・コード・数値のみの要素は変更せずそのまま返すこと。
3. 出力は入力と同じ件数・同じ順序の文字列配列（JSON配列）のみ。説明や前置き、コードフェンスは付けないこと。`;

const MAX_CACHE_ENTRIES = 4000;
const MAX_BATCH_CHARS = 20000; // keeps one request well under typical output-token limits

const cache = new Map<string, string>();

// Hiragana, katakana, CJK ideographs, and CJK compatibility ideographs.
const JAPANESE_PATTERN = /[぀-ヿ㐀-䶿一-鿿豈-﫿]/;
const LETTER_PATTERN = /[A-Za-zÀ-ɏ]/;

function needsTranslation(text: string): boolean {
  if (!text.trim()) return false;
  if (JAPANESE_PATTERN.test(text)) return false;
  return LETTER_PATTERN.test(text);
}

/**
 * A short, fast, content-addressed key. A hash collision would only ever
 * reuse one short sentence's translation for a different one — harmless
 * for this use, and implausible at the few-thousand-entry scale this cache
 * runs at — so a full cryptographic hash isn't worth pulling in.
 */
function cacheKey(text: string): string {
  let hash = 0;
  for (let i = 0; i < text.length; i++) {
    hash = (hash * 31 + text.charCodeAt(i)) | 0;
  }
  return `${text.length}:${hash}`;
}

function remember(key: string, value: string): void {
  if (cache.size >= MAX_CACHE_ENTRIES) {
    const oldest = cache.keys().next().value;
    if (oldest !== undefined) cache.delete(oldest);
  }
  cache.set(key, value);
}

interface GeminiGenerateResponse {
  candidates?: { content?: { parts?: { text?: string }[] } }[];
}

async function callGemini(texts: string[]): Promise<string[]> {
  const apiKey = env.geminiApiKey();
  if (!apiKey) throw new Error("GEMINI_API_KEY is not set");
  const model = env.translateModel();
  const url = `${env.geminiApiBase()}/v1beta/models/${model}:generateContent?key=${apiKey}`;

  let response: Response;
  try {
    response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      cache: "no-store",
      signal: AbortSignal.timeout(20000),
      body: JSON.stringify({
        systemInstruction: { parts: [{ text: DEFAULT_SYSTEM_PROMPT }] },
        contents: [{ parts: [{ text: JSON.stringify(texts) }] }],
        generationConfig: {
          responseMimeType: "application/json",
          responseSchema: { type: "ARRAY", items: { type: "STRING" } },
          maxOutputTokens: 8192,
        },
      }),
    });
  } catch (cause) {
    throw new Error(
      `Gemini translate request failed: ${(cause as Error).message}`,
    );
  }

  if (!response.ok) {
    const body = await response.text().catch(() => "");
    throw new Error(
      `Gemini translate returned HTTP ${response.status}: ${body.slice(0, 300)}`,
    );
  }

  const payload = (await response.json()) as GeminiGenerateResponse;
  const raw = payload.candidates?.[0]?.content?.parts?.[0]?.text;
  if (!raw) throw new Error("Gemini translate returned no text");

  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    throw new Error(
      `Gemini translate returned non-JSON text: ${raw.slice(0, 200)}`,
    );
  }
  if (!Array.isArray(parsed) || parsed.length !== texts.length) {
    const got = Array.isArray(parsed)
      ? `an array of ${parsed.length}`
      : typeof parsed;
    throw new Error(
      `Gemini translate returned ${got} item(s) for ${texts.length} input(s)`,
    );
  }
  return parsed.map((item) => (typeof item === "string" ? item : String(item)));
}

/** Splits into chunks small enough for one Gemini call's output budget. */
function chunk(texts: string[]): string[][] {
  const chunks: string[][] = [];
  let current: string[] = [];
  let currentChars = 0;
  for (const text of texts) {
    if (current.length > 0 && currentChars + text.length > MAX_BATCH_CHARS) {
      chunks.push(current);
      current = [];
      currentChars = 0;
    }
    current.push(text);
    currentChars += text.length;
  }
  if (current.length > 0) chunks.push(current);
  return chunks;
}

/**
 * Translates every distinct, not-yet-cached, non-Japanese string in
 * `texts`, and returns a map from each original string to its Japanese
 * translation. A string that didn't need translating, or that translation
 * is unavailable/failed for, maps to itself — so a caller can always look
 * any string up and get something sensible back.
 */
export async function translateBatch(
  texts: string[],
): Promise<Map<string, string>> {
  const result = new Map<string, string>();
  const pending: string[] = [];
  const pendingKeys: string[] = [];

  for (const text of texts) {
    if (result.has(text)) continue; // already resolved earlier in this same call
    if (!needsTranslation(text)) {
      result.set(text, text);
      continue;
    }
    const key = cacheKey(text);
    const cached = cache.get(key);
    if (cached !== undefined) {
      result.set(text, cached);
      continue;
    }
    pending.push(text);
    pendingKeys.push(key);
  }

  if (pending.length === 0) return result;

  if (!env.geminiApiKey()) {
    for (const text of pending) result.set(text, text);
    return result;
  }

  let cursor = 0;
  for (const batch of chunk(pending)) {
    try {
      const translated = await callGemini(batch);
      for (let i = 0; i < batch.length; i++) {
        const original = batch[i];
        const value = translated[i] ?? original;
        remember(pendingKeys[cursor + i], value);
        result.set(original, value);
      }
    } catch (error) {
      console.error("translation failed, showing original text:", error);
      for (const original of batch) result.set(original, original);
    }
    cursor += batch.length;
  }

  return result;
}
