import { NextRequest, NextResponse } from "next/server";

import { getConversationWithTranscript, OmiApiError } from "@/lib/omi";

/**
 * Fetches one conversation's transcript on demand. This is the only place
 * in the app that spends Omi's scarce 25/hr transcript-read budget — the
 * detail page itself renders from the already-fetched, transcript-free list
 * data, and only calls this when the person explicitly asks to see the
 * transcript (see TranscriptLoader.tsx).
 *
 * Auth note: this route is covered by middleware.ts's matcher like every
 * other route, so it is not reachable without a valid session cookie.
 */
export async function GET(
  _request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;

  try {
    const conversation = await getConversationWithTranscript(id);
    const segments = (conversation.transcript_segments ?? []).map(
      (segment) => ({
        speaker: segment.is_user ? "自分" : segment.speaker || "話者不明",
        text: segment.text,
        startSeconds: segment.start ?? 0,
      }),
    );
    return NextResponse.json({ segments });
  } catch (error) {
    if (error instanceof OmiApiError) {
      return NextResponse.json(
        { error: error.message },
        { status: error.status ?? 502 },
      );
    }
    return NextResponse.json({ error: "unexpected error" }, { status: 500 });
  }
}
