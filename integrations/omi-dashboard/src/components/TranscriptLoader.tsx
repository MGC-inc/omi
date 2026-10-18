"use client";

import { useState } from "react";

interface Segment {
  speaker: string;
  text: string;
  startSeconds: number;
}

function formatClock(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

/**
 * Fetches the transcript only when asked — see api/transcript/[id]/route.ts
 * for why: it is the one call that spends Omi's 25/hr transcript budget, so
 * loading it on every detail-page view would be wasteful for data nobody
 * necessarily wants to read in full.
 */
export function TranscriptLoader({
  conversationId,
}: {
  conversationId: string;
}) {
  const [state, setState] = useState<"idle" | "loading" | "error" | "loaded">(
    "idle",
  );
  const [segments, setSegments] = useState<Segment[]>([]);
  const [error, setError] = useState<string>("");

  async function load() {
    setState("loading");
    try {
      const response = await fetch(`/api/transcript/${conversationId}`);
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "読み込みに失敗しました");
      setSegments(data.segments ?? []);
      setState("loaded");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setState("error");
    }
  }

  if (state === "idle") {
    return (
      <button
        onClick={load}
        className="rounded-lg border border-border px-3 py-1.5 text-sm text-foreground hover:border-accent"
      >
        文字起こしを表示
      </button>
    );
  }

  if (state === "loading") {
    return <p className="text-sm text-muted">読み込み中…</p>;
  }

  if (state === "error") {
    return (
      <p className="text-sm text-muted">
        文字起こしを取得できませんでした: {error}
      </p>
    );
  }

  if (segments.length === 0) {
    return <p className="text-sm text-muted">文字起こしはありません。</p>;
  }

  return (
    <div className="space-y-1.5 text-sm">
      {segments.map((segment, index) => (
        <p key={index} className="text-foreground">
          <span className="mr-2 text-xs text-muted">
            {formatClock(segment.startSeconds)}
          </span>
          <span className="mr-1 font-medium">{segment.speaker}:</span>
          {segment.text}
        </p>
      ))}
    </div>
  );
}
